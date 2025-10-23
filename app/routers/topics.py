from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from pathlib import Path
import json, os, logging, re, random, copy, requests

from app.db import get_db
from app.deps import get_current_user
from app.models.user import User
from app.models.topic import Topic
from app.models.user_topic import UserTopic
from app.models.topic_session import TopicSession

# AI
from app.ai.gemini import (
    generate_explanation,
    generate_exercises_variant,
    fallback_generate_exercises,
    fallback_generate_explanation,
    generate_one_image_png,
)
from app.ai.variation_utils import extract_used_fractions

# Badges / puntos
from app.domain.badges.service import on_points_changed
from app.models.badge import Badge
from app.models.user_badge import UserBadge

# === HELPERS GENERALES (static, media, content, tts, imgs, text) ===
from app.core.settings_static import (
    STATIC_DIR, TTS_DIR, GEN_DIR, static_url_for
)
from app.core.utils_imgs import (
    make_explanation_figure_png,
    decorate_visuals_for_items,
    ensure_fraction_png,
    save_png_return_url,
    pick_visual_expl_image_from_ctx
)
from app.core.utils_text import neutralize_audio_words
from app.core.utils_tts import make_tts, tts_url_for
from app.core.content import resolve_context_path

# === HELPERS DE FRACCIONES (MOVIDOS DEL ROUTER) ===
# Se importan tal cual para no romper firmas ni comportamiento.
from app.core.engines.grades.grade3.fracciones_basicas import (
    _parse_frac,
    _rand_frac_not_equiv,
    _sanitize_mcq,
    _shuffle_choices_set_correct,
    _synth_question_from_choices,
    _argmax_frac_index,
    _argmin_frac_index,
    FRACTION_RE,
)

from app.core.engines.registry import get_engine_for_slug

log = logging.getLogger("topics")

def _choose_reuse_mode(policy: dict | None, run_idx: int) -> str:
    """Devuelve 'ai_or_cached' | 'bank' | 'variations_from_bank'."""
    p = policy or {}
    if run_idx <= 1:
        return (p.get("first_run") or "ai_or_cached").lower()
    if run_idx == 2:
        return (p.get("second_run") or "bank").lower()
    return (p.get("later_runs") or "variations_from_bank").lower()

# -------------------------------------------------------------------
# Helpers de media que sí permanecen aquí 
# -------------------------------------------------------------------

def _norm_media_url(raw: str | None) -> str | None:
    if not raw:
        return None
    u = raw.strip()
    if u.startswith("http://") or u.startswith("https://"):
        return u
    if u.startswith("/media/"):
        return u
    if u.startswith("/covers/") or u.startswith("/avatars/") or u.startswith("/badges/"):
        return f"/media{u}"
    if u.startswith("covers/") or u.startswith("avatars/") or u.startswith("badges/"):
        return f"/media/{u}"
    # último recurso
    return f"/media/{u.lstrip('/')}"

def _resolve_or_generate_visual_image(db: Session, ut: UserTopic, ctx: dict, topic_slug: str) -> str | None:
    try:
        if ut.cached_visual_image_url:
            return ut.cached_visual_image_url

        prompts = (ctx.get("visual_assets") or {}).get("image_prompts") or []
        if prompts:
            prompt = prompts[0]  # ← vuelve a tu prompt del JSON
            png = generate_one_image_png(prompt)
            if png:
                url = save_png_return_url(topic_slug, png)
                ut.cached_visual_image_url = url
                db.add(ut); db.commit(); db.refresh(ut)
                return url
    except Exception as e:
        log.warning("visual image resolve/gen failed: %s", e)
    return None

def _pick_visual_expl_image(ctx: dict) -> str | None:
    """
    Intenta tomar una imagen ya existente declarada en el JSON.
    Si viene un path empezando con /static lo devolvemos tal cual.
    """
    imgs = (ctx.get("visual_assets") or {}).get("images") or []
    if imgs:
        url = imgs[0]
        # Permite rutas absolutas tipo /static/...
        return url if url.startswith("/") else f"/static/{url.lstrip('/')}"

    # Si no hay, intenta un cover del tema como explicación visual
    cover = (ctx.get("cover") or "").strip()
    if cover:
        return cover if cover.startswith("/") else f"/static/{cover.lstrip('/')}"
    return None

# -------------------------------------------------------------------
# Core: abrir/continuar sesión
# -------------------------------------------------------------------

def _open_session_core(
    db: Session,
    me: User,
    ut: UserTopic,
    t: Topic,
    force_new: bool = False
):
    style = (ut.recommended_style or me.vak_style or "visual").strip().lower()

    # Contexto
    path = resolve_context_path(t.grade, t.slug)
    if not path.exists():
        raise HTTPException(404, f"Contexto no encontrado: {path}")
    ctx = json.loads(path.read_text(encoding="utf-8"))

    # Crear nueva sesión si no hay o si terminó; force_new respeta reset manual
    last = db.execute(
        select(TopicSession)
        .where(TopicSession.user_id == me.id, TopicSession.topic_id == t.id)
        .order_by(TopicSession.id.desc())
    ).scalars().first()

    need_new = (
        force_new
        or (not last)
        or (last.current_index >= 10)
        or int(ut.times_opened or 0) == 0
    )

    # Evitar repetir fracciones recientes (opcional; mantiene tu UX)
    avoid_numbers: list[int] = []
    try:
        prev = db.execute(
            select(TopicSession)
            .where(TopicSession.user_id == me.id, TopicSession.topic_id == t.id)
            .order_by(TopicSession.id.desc()).limit(5)
        ).scalars().all()
        for s in prev:
            if s.items:
                avoid_numbers += extract_used_fractions(s.items)
    except Exception:
        avoid_numbers = []

    explanation = None

    try:
        if need_new:
            engine = get_engine_for_slug(t.grade, t.slug)

            payload = engine.build_session(
                context_json=ctx,
                style=style,
                avoid_numbers=avoid_numbers,
                seed=None,
                reuse_mode=None,   # ignorado
            )

            items = payload.get("items") or []
            # Pase de consistencia final (índice correcto por texto)
            try:
                for it in items:
                    if isinstance(it, dict) and it.get("type") == "multiple_choice":
                        choices = [str(c).strip() for c in (it.get("choices") or []) if str(c).strip()]

                        qtxt = (it.get("question") or "").strip()
                        synthesized = False
                        if not qtxt or qtxt.lower().startswith("elige la opción correcta"):
                            it["question"] = _synth_question_from_choices(choices)
                            synthesized = True

                        qlow = (it.get("question") or "").lower()

                        # Si sintetizamos (o si la IA trajo algo genérico), ajusta la correcta
                        if synthesized:
                            if "más grande" in qlow or "mayor" in qlow:
                                idx = _argmax_frac_index(choices)
                                if idx is not None:
                                    it["correct_index"] = idx
                            elif "más pequeña" in qlow or "menor" in qlow:
                                idx = _argmin_frac_index(choices)
                                if idx is not None:
                                    it["correct_index"] = idx

                        # por si acaso:
                        if not (it.get("question") or "").strip():
                            it["question"] = "Elige la opción correcta."
            except Exception as e:
                log.warning("last-mile question synthesis failed: %s", e)

            explanation = payload.get("explanation")

            # Imagen/visual (si aplica al estilo)
            try:
                if style == "visual":
                    from app.core.utils_imgs import make_explanation_figure_png
                    base = explanation or (ctx.get("summary") or t.title)
                    ut.cached_visual_image_url = make_explanation_figure_png(t.id, me.id, base)
            except Exception as e:
                log.warning("visual expl generation failed: %s", e)

            # Normaliza a 10
            items = (items[:10] if len(items) > 10 else items)
            if len(items) < 10:
                engine = get_engine_for_slug(t.grade, t.slug)
                repaired = engine.validate_repair(items, ctx)
                items = repaired[:10] if repaired else items
                
            try:
                if style == "visual" and int(ut.times_opened or 0) == 0:
                    decorate_visuals_for_items(items, t.id, me.id)
            except Exception as e:
                log.warning("decorate visuals failed: %s", e)

            last = TopicSession(
                user_id=me.id, topic_id=t.id, style_used=style,
                items=items,
                results=[{"correct": None, "attempts": 0} for _ in range(10)],
                current_index=0,
                explanation=explanation
            )
            db.add(last); db.commit(); db.refresh(last)

            ut.times_opened = int(ut.times_opened or 0) + 1
            ut.ai_seed_done = True
            ut.cached_explanation = explanation
            db.add(ut); db.commit(); db.refresh(ut)
        else:
            explanation = last.explanation or (ut.cached_explanation or None)

        # Reparación ligera en reuso (por si quedaron MCQ raras)
        try:
            engine = get_engine_for_slug(t.grade, t.slug)
            fixed = engine.validate_repair(last.items or [], ctx)
            if fixed != (last.items or []):
                last.items = fixed
                db.add(last); db.commit(); db.refresh(last)
        except Exception as e:
            log.warning("validate_repair (reuse) failed: %s", e)

    except Exception as e:
        log.error("open_session_core failed: %s", e)
        raise HTTPException(500, "No se pudo abrir el tema, intenta de nuevo.")

    # Assets por estilo: TTS sólo auditivo (igual que antes)
    explanation_audio_url = None
    if style == "auditivo":
        try:
            if ut.cached_expl_audio_url:
                explanation_audio_url = ut.cached_expl_audio_url
            else:
                exp_path = (TTS_DIR / f"sess-{last.id}-explanation.wav")
                if not exp_path.exists():
                    voice_env = os.getenv("TTS_VOICE", "").strip()
                    fallback_voices = [v for v in [voice_env, "es-ES-Standard-A", "es-US-Standard-A", "es-ES-Neural2-A"] if v]
                    for v in fallback_voices:
                        try:
                            make_tts(last.explanation or explanation or "", exp_path, voice=v)
                            break
                        except Exception as e:
                            log.warning("tts voice failed (%s): %s", v, e)
                if exp_path.exists():
                    explanation_audio_url = tts_url_for(last.id, "explanation")
                    ut.cached_expl_audio_url = explanation_audio_url
                    db.add(ut); db.commit()
        except Exception as e:
            log.warning("tts explanation fail: %s", e)

    progress_in_session = min((last.current_index or 0) * 10, 100)

    return {
        "sessionId": last.id,
        "title": t.title,
        "style": last.style_used,
        "explanation": explanation or last.explanation,
        "explanationAudioUrl": explanation_audio_url if style == "auditivo" else None,
        "explanationImageUrl": (ut.cached_visual_image_url or None) if style == "visual" else None,
        "currentIndex": last.current_index,
        "items": last.items,
        "progressInSession": progress_in_session,
    }

# -------------------------------------------------------------------
# Router
# -------------------------------------------------------------------

router = APIRouter(prefix="/topics", tags=["topics"])

@router.get("/catalog")
def catalog(db: Session = Depends(get_db)):
    rows = db.execute(select(Topic)).scalars().all()
    out: dict[int, list] = {}
    for t in rows:
        cover = _norm_media_url(t.cover_url)
        out.setdefault(int(t.grade), []).append({
            "id": t.id,
            "slug": t.slug,
            "title": t.title,
            "coverUrl": cover,
        })
    return out

@router.get("/my")
def my_topics(db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    q = db.execute(
        select(UserTopic, Topic)
        .where(UserTopic.user_id == me.id)
        .join(Topic, Topic.id == UserTopic.topic_id)
    ).all()
    res = []
    for ut, t in q:
        res.append({
            "userTopicId": ut.id,
            "topicId": t.id,
            "slug": t.slug,
            "title": t.title,
            "coverUrl": _norm_media_url(t.cover_url),
            "progressPct": ut.progress_pct,
            "recommendedStyle": ut.recommended_style,
            "completedCount": ut.completed_count
        })
    return res

@router.post("/add/{topic_id}")
def add_topic(topic_id: int, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    exists = db.execute(
        select(UserTopic.id).where(UserTopic.user_id == me.id, UserTopic.topic_id == topic_id)
    ).scalar_one_or_none()
    if exists:
        return {"ok": True, "userTopicId": exists}

    ut = UserTopic(
        user_id=me.id,
        topic_id=topic_id,
        progress_pct=0,
        completed_count=0,
        total_time_sec=0,
        attempts_total=0,
        errors_total=0,
        best_score_pct=0,
        last_score_pct=0,
        last_time_sec=0,
        ai_seed_done=False,
        cached_explanation=None,
        cached_expl_audio_url=None,
        cached_visual_image_url=None,
        bank_variation_seed=None,
        times_opened=0,
        bank_version=1,
        bank_variant_counter=0,
    )
    db.add(ut); db.commit(); db.refresh(ut)
    return {"ok": True, "userTopicId": ut.id}

@router.post("/{user_topic_id}/open")
def open_session(user_topic_id: int, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    ut = db.get(UserTopic, user_topic_id)
    if not ut or ut.user_id != me.id:
        raise HTTPException(404, "Tema no encontrado")
    t = db.get(Topic, ut.topic_id)
    if not t:
        raise HTTPException(404, "Topic asociado no existe")
    return _open_session_core(db, me, ut, t)

@router.post("/slug/{slug}/open")
def open_session_by_slug(
    slug: str,
    reset: bool = False,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    t = db.execute(select(Topic).where(Topic.slug == slug)).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Tema no encontrado")

    ut = db.execute(
        select(UserTopic).where(UserTopic.user_id == me.id, UserTopic.topic_id == t.id)
    ).scalar_one_or_none()
    if not ut:
        raise HTTPException(404, "Aún no añadiste este tema")

    already_completed = (int(ut.progress_pct or 0) >= 100)

    # ⚡ Ruta rápida: si ya lo completó y NO pidió reset → NO generes nada
    if already_completed and not reset:
        return {
            "alreadyCompleted": True,
            "title": t.title,
            "style": ut.recommended_style or (me.vak_style or "visual"),
            "sessionId": None,
            "currentIndex": 0,
            "items": [],
            "progressInSession": 100,
            "explanation": None,
            "explanationAudioUrl": None,
        }

    # Si pidió reset (o no estaba completo), abre/crea sesión normal
    payload = _open_session_core(db, me, ut, t, force_new=reset)
    payload["alreadyCompleted"] = already_completed
    return payload

@router.post("/session/{session_id}/answer")
def answer(session_id: int, body: dict, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    sess = db.get(TopicSession, session_id)
    if not sess or sess.user_id != me.id:
        raise HTTPException(404)

    idx = int(body.get("index", 0))
    total_items = len(sess.items or [])
    if idx < 0 or idx >= total_items:
        raise HTTPException(400, "Índice fuera de rango")
    if idx != int(sess.current_index or 0):
        raise HTTPException(400, "Índice fuera de secuencia")

    answer = body.get("answer")
    item = (sess.items or [])[idx] or {}

    def check(it, ans):
        t = it.get("type")
        if t == "multiple_choice":
            try:
                return int(ans) == int(it.get("correct_index", -1))
            except:
                return False
        if t == "match_pairs":
            return ans == it.get("pairs")
        if t == "drag_to_bucket":
            sol = it.get("solution") or {}
            if not isinstance(ans, dict) or not isinstance(sol, dict):
                return False
            if set(ans.keys()) != set(sol.keys()):
                return False
            for b in sol.keys():
                if set(ans.get(b) or []) != set(sol.get(b) or []):
                    return False
            return True
        return False

    sess.attempts_cnt = int(sess.attempts_cnt or 0) + 1
    sess.results[idx]["attempts"] = int(sess.results[idx].get("attempts") or 0) + 1

    correct = check(item, answer)
    sess.results[idx]["correct"] = bool(correct)

    if not correct:
        item["__wrongAttempts"] = int(item.get("__wrongAttempts") or 0) + 1
        sess.mistakes_cnt = int(sess.mistakes_cnt or 0) + 1
        feedback = item.get("explain", "Revisa el concepto clave y vuelve a intentar.")
    else:
        sess.score_raw = int(sess.score_raw or 0) + 1
        sess.current_index = min(int(sess.current_index or 0) + 1, total_items)
        feedback = None

    # precisión por intentos
    sess.score_pct = round(100.0 * (int(sess.score_raw or 0) / max(1, int(sess.attempts_cnt or 0))))

    finished = (int(sess.current_index or 0) >= total_items)

    # Recomendación simple
    total_answered = min(int(sess.current_index or 0), total_items)
    wrong = sum(1 for r in (sess.results[:total_answered] if sess.results else []) if r.get("correct") is False)
    recommended = None
    if total_answered >= 5 and (wrong / max(1, total_answered)) > 0.4:
        nxt = {"visual": "auditivo", "auditivo": "kinestesico", "kinestesico": "visual"}
        recommended = nxt.get(sess.style_used, "visual")
        ut_tmp = db.execute(
            select(UserTopic).where(UserTopic.user_id == me.id, UserTopic.topic_id == sess.topic_id)
        ).scalar_one_or_none()
        if ut_tmp and ut_tmp.recommended_style != recommended:
            ut_tmp.recommended_style = recommended
            db.add(ut_tmp)

    ut = db.execute(
        select(UserTopic).where(UserTopic.user_id == me.id, UserTopic.topic_id == sess.topic_id)
    ).scalar_one_or_none()
    progress_pct = min(int(sess.current_index or 0) * 10, 100)
    if ut:
        ut.progress_pct = progress_pct

        # actualiza tiempo total aproximado
        try:
            inc = int(body.get("elapsedSec") or 0)
            if inc > 0:
                ut.last_time_sec = int(ut.last_time_sec or 0) + inc
        except:
            pass

        db.add(ut)

    db.add(sess)
    db.commit()
    db.refresh(sess)
    if ut:
        db.refresh(ut)

    return {
        "correct": correct,
        "feedback": feedback,
        "nextIndex": int(sess.current_index or 0),
        "recommendedStyle": recommended,
        "finished": finished,
        "progressPct": progress_pct,
    }

@router.post("/session/{session_id}/finish")
def finish(session_id: int, body: dict | None = None, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    """
    Marca la sesión como completada, actualiza estadísticas en UserTopic y otorga puntos.
    body opcional: {"timeSec": number}
    Bonus:
      +50 si NO hubo ningún error (mistakes_cnt == 0)
    """
    sess = db.get(TopicSession, session_id)
    if not sess or sess.user_id != me.id:
        raise HTTPException(404, "Sesión no encontrada")

    total_items = len(sess.items or [])
    if int(sess.current_index or 0) < total_items:
        raise HTTPException(400, "Sesión incompleta")

    # guarda tiempo
    time_sec = 0
    if isinstance(body, dict):
        try:
            time_sec = int(body.get("timeSec") or 0)
        except:
            time_sec = 0
    sess.elapsed_sec = int(time_sec or 0)

    # ---- UserTopic ----
    ut = db.execute(
        select(UserTopic).where(UserTopic.user_id == me.id, UserTopic.topic_id == sess.topic_id)
    ).scalar_one_or_none()
    if not ut:
        raise HTTPException(404, "Tema del usuario no encontrado")

    ut.progress_pct    = 100
    ut.completed_count = int(ut.completed_count or 0) + 1
    ut.attempts_total  = int(ut.attempts_total or 0) + int(sess.attempts_cnt or 0)
    ut.errors_total    = int(ut.errors_total  or 0) + int(sess.mistakes_cnt or 0)
    ut.last_score_pct  = int(sess.score_pct or 0)        # precisión %
    ut.last_time_sec   = int(sess.elapsed_sec or 0)

    if ut.best_score_pct is None or int(sess.score_pct or 0) > int(ut.best_score_pct or 0):
        ut.best_score_pct = int(sess.score_pct or 0)
    if sess.elapsed_sec and (ut.best_time_sec is None or int(sess.elapsed_sec) < int(ut.best_time_sec or 0)):
        ut.best_time_sec = int(sess.elapsed_sec)

    db.add(ut)

    # ---- Puntos / Insignias ----
    awarded = []
    if not bool(sess.points_awarded):
        me_db = db.get(User, me.id)
        old_points = int(me_db.points or 0)

        # Base 100 + bonus “sin errores”
        bonus = 50 if int(sess.mistakes_cnt or 0) == 0 else 0
        new_points = old_points + 100 + bonus
        me_db.points = new_points

        sess.points_awarded = True
        db.add(sess); db.add(me_db)
        db.commit()

        slugs = on_points_changed(db, me.id, old_points=old_points, new_points=new_points)
        if slugs:
            rows = db.execute(select(Badge).where(Badge.slug.in_(slugs))).scalars().all()
            for b in rows:
                owners = db.execute(
                    select(func.count(func.distinct(UserBadge.user_id))).where(UserBadge.badge_id == b.id)
                ).scalar_one() or 0
                total_users = db.execute(select(func.count(User.id))).scalar_one() or 1
                rarity = round(owners * 100.0 / total_users, 2)
                awarded.append({
                    "id": b.id,
                    "slug": b.slug,
                    "title": b.title,
                    "imageUrl": b.image_url,
                    "rarityPct": rarity,
                    "owned": True
                })
    else:
        db.commit()

    total_attempts = int(sess.attempts_cnt or 0)
    total_correct  = int(sess.score_raw or 0)
    mistakes       = int(sess.mistakes_cnt or 0)
    precision_pct  = round(100.0 * total_correct / max(1, total_attempts))

    me_db2 = db.get(User, me.id)
    return {
        "ok": True,
        "awardedBadges": awarded,
        "points": int(me_db2.points or 0),
        "timeSec": int(sess.elapsed_sec or 0),
        "mistakes": mistakes,
        "precisionPct": precision_pct
    }

@router.post("/save/{session_id}")
def save_progress(session_id: int, current_index: int | None = None, elapsed_sec: int | None = None,
                    db: Session = Depends(get_db), me=Depends(get_current_user)):
    s = db.get(TopicSession, session_id)
    if not s or s.user_id != me.id:
        raise HTTPException(404, "Sesión no encontrada")

    # Actualiza índice si viene desde el front (si no, usa el de la sesión)
    if isinstance(current_index, int):
        s.current_index = max(0, min(10, current_index))

    # Acumula tiempo si quieres
    if isinstance(elapsed_sec, int):
        ut = db.query(UserTopic).filter_by(user_id=me.id, topic_id=s.topic_id).first()
        if ut:
            ut.last_time_sec = (ut.last_time_sec or 0) + max(0, elapsed_sec)

    # Refleja progreso en user_topics
    ut = db.query(UserTopic).filter_by(user_id=me.id, topic_id=s.topic_id).first()
    if ut:
        # 0..10 preguntas -> 0..100%
        ut.progress_pct = min((s.current_index or 0) * 10, 100)

    db.commit()
    return {"ok": True, "progressPct": ut.progress_pct if ut else 0}