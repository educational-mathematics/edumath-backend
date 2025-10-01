# app/routers/topics.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from pathlib import Path
import json, os, logging, uuid, requests

from app.db import get_db
from app.deps import get_current_user
from app.models.user import User
from app.models.topic import Topic
from app.models.user_topic import UserTopic
from app.models.topic_session import TopicSession

from app.ai.gemini import (
    generate_explanation,
    generate_exercises_variant,
    fallback_generate_exercises,
    fallback_generate_explanation,
)
from app.ai.variation_utils import extract_used_fractions

# insignias / puntos
from app.domain.badges.service import on_points_changed
from app.models.badge import Badge
from app.models.user_badge import UserBadge

log = logging.getLogger("topics")

APP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_DIR.parent
CONTENT_DIR = Path(os.getenv("CONTENT_DIR", APP_DIR / "content"))

STATIC_DIR = (APP_DIR / "static").resolve()
STATIC_DIR.mkdir(parents=True, exist_ok=True)

TTS_DIR = (STATIC_DIR / "tts")
TTS_DIR.mkdir(parents=True, exist_ok=True)

STATIC_GEN = Path(os.getenv("STATIC_GEN_DIR", STATIC_DIR / "generated"))
STATIC_GEN.mkdir(parents=True, exist_ok=True)


# -------------------------------
# Helpers
# -------------------------------
def _make_tts(text: str, out_path: Path, voice: str = ""):
    """
    Llama a tu endpoint interno /ai/tts para generar audio WAV.
    Si falla, no levanta error (solo no genera audio).
    """
    voice = (voice or os.getenv("TTS_VOICE", "es-ES-Standard-A")).strip()
    try:
        r = requests.post(
            "http://localhost:8000/ai/tts",
            json={"text": text, "voice": voice},
            timeout=30,
        )
        if r.status_code == 200 and r.content:
            out_path.write_bytes(r.content)
        else:
            # 204 o 5xx: sin audio
            pass
    except Exception as e:
        log.warning("[topics._make_tts] TTS error: %s", e)


def _tts_url_for(session_id: int, name: str) -> str:
    return f"/static/tts/sess-{session_id}-{name}.wav"


def _neutralize_audio_words(question: str) -> str:
    if not question:
        return question
    q = question.strip()
    lowers = q.lower()
    if lowers.startswith("escucha ") or "escucha atentamente" in lowers or "te dictan" in lowers:
        return (
            q.replace("Escucha atentamente ", "Lee atentamente ")
             .replace("Escucha ", "Lee ")
             .replace("te dictan", "se presentan")
        )
    return q


def _save_png_return_url(topic_slug: str, png_bytes: bytes) -> str:
    name = f"{topic_slug}-{uuid.uuid4().hex[:8]}.png"
    out = STATIC_GEN / name
    out.write_bytes(png_bytes)
    return f"/static/generated/{name}"


def resolve_context_path(grade: int, slug: str) -> Path:
    p = CONTENT_DIR / f"grade-{grade}" / f"{slug}.json"
    if p.exists():
        return p
    fallback = REPO_ROOT / "content" / f"grade-{grade}" / f"{slug}.json"
    if fallback.exists():
        return fallback
    return p  # para que el 404 muestre la ruta esperada


# -------------------------------
# Core: abrir/continuar sesión
# -------------------------------
def _open_session_core(
    db: Session,
    me: User,
    ut: UserTopic,
    t: Topic,
    force_new: bool = False
):
    style = ut.recommended_style or (me.vak_style or "visual")

    # Contexto
    path = resolve_context_path(t.grade, t.slug)
    if not path.exists():
        log.warning("open_session: contexto no encontrado. grade=%s slug=%s path=%s", t.grade, t.slug, str(path))
        raise HTTPException(404, f"Contexto no encontrado: {path}")

    ctx = json.loads(path.read_text(encoding="utf-8"))

    # Buscar última sesión
    last = db.execute(
        select(TopicSession)
        .where(TopicSession.user_id == me.id, TopicSession.topic_id == t.id)
        .order_by(TopicSession.id.desc())
    ).scalars().first()

    need_new = force_new or (not last) or (last.current_index >= 10)

    if need_new:
        # Evitar repetir números (fracciones), tomando las últimas sesiones
        prev = db.execute(
            select(TopicSession)
            .where(TopicSession.user_id == me.id, TopicSession.topic_id == t.id)
            .order_by(TopicSession.id.desc()).limit(5)
        ).scalars().all()
        avoid_numbers = []
        for s in prev:
            if s.items:
                avoid_numbers += extract_used_fractions(s.items)

        try:
            items = generate_exercises_variant(ctx, style=style, avoid_numbers=avoid_numbers)
            explanation = generate_explanation(ctx)
        except Exception as e:
            log.warning("open_session: IA falló, fallback. err=%s", e)
            items = fallback_generate_exercises(ctx, style, avoid_numbers)
            explanation = fallback_generate_explanation(ctx)

        # Defensa mínima por si el modelo devuelve <10
        if not isinstance(items, list):
            items = []
        # recorta/expande a 10
        if len(items) > 10:
            items = items[:10]
        while len(items) < 10:
            items.append({
                "type": "multiple_choice",
                "question": "Elige la opción correcta.",
                "choices": ["Correcta", "Incorrecta 1", "Incorrecta 2", "Incorrecta 3"],
                "correct_index": 0,
                "explain": "Revisa el concepto clave."
            })

        last = TopicSession(
            user_id=me.id, topic_id=t.id, style_used=style,
            items=items,
            results=[{"correct": None, "attempts": 0} for _ in range(10)],
            current_index=0,
            explanation=explanation,
            attempts_cnt=0,      # ← total de intentos (sube en cada submit)
            mistakes_cnt=0,      # ← total de fallos (sube cuando incorrecto)
            score_raw=0,         # ← aciertos totales (sube cuando correcto)
            score_pct=0,         # ← precisión mostrada
        )
        db.add(last); db.commit(); db.refresh(last)
    else:
        explanation = last.explanation or None

    # Genera audio para la explicación si es auditivo
    explanation_audio_url = None
    try:
        if last.style_used == "auditivo" and (last.explanation or explanation):
            exp_path = (TTS_DIR / f"sess-{last.id}-explanation.wav")
            if not exp_path.exists():
                _make_tts(last.explanation or explanation, exp_path, voice=os.getenv("TTS_VOICE", "es-ES-Standard-A"))
            if exp_path.exists():
                explanation_audio_url = _tts_url_for(last.id, "explanation")
    except Exception as e:
        log.warning("tts explanation fail: %s", e)

    # Normaliza preguntas “auditivas” si no hay tts
    if last.style_used == "auditivo":
        changed = False
        for idx, it in enumerate(last.items or []):
            if it.get("type") == "multiple_choice":
                # Si no hay audio, evita "Escucha…"
                q = it.get("question") or ""
                q2 = _neutralize_audio_words(q)
                if q2 != q:
                    it["question"] = q2
                    changed = True
        if changed:
            db.add(last); db.commit(); db.refresh(last)

    progress_in_session = min((last.current_index or 0) * 10, 100)

    return {
        "sessionId": last.id,
        "title": t.title,
        "style": last.style_used,
        "explanation": explanation or last.explanation,
        "explanationAudioUrl": explanation_audio_url if style == "auditivo" else None,
        "currentIndex": last.current_index,
        "items": last.items,
        "progressInSession": progress_in_session,
    }


# -------------------------------
# Router
# -------------------------------
router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("/catalog")
def catalog(db: Session = Depends(get_db)):
    rows = db.execute(select(Topic)).scalars().all()
    out: dict[int, list] = {}
    for t in rows:
        out.setdefault(int(t.grade), []).append({
            "id": t.id, "slug": t.slug, "title": t.title, "coverUrl": t.cover_url
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
            "coverUrl": t.cover_url,
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
    ut = UserTopic(user_id=me.id, topic_id=topic_id, progress_pct=0)
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

    # 🔢 SIEMPRE cuenta intento
    sess.attempts_cnt = int(sess.attempts_cnt or 0) + 1
    sess.results[idx]["attempts"] = int(sess.results[idx].get("attempts") or 0) + 1

    correct = check(item, answer)
    sess.results[idx]["correct"] = bool(correct)

    if not correct:
        # ❌ fallo → suma errores y marca en el ítem (para reglas posteriores)
        sess.mistakes_cnt = int(sess.mistakes_cnt or 0) + 1
        item["__wrongAttempts"] = int(item.get("__wrongAttempts") or 0) + 1
        feedback = item.get("explain", "Revisa el concepto clave y vuelve a intentar.")
    else:
        # ✅ acierto → avanza índice y suma aciertos
        sess.score_raw = int(sess.score_raw or 0) + 1
        sess.current_index = min(int(sess.current_index or 0) + 1, total_items)
        feedback = None

    # 🎯 precisión por intentos
    sess.score_pct = round(100.0 * (int(sess.score_raw or 0) / max(1, int(sess.attempts_cnt or 0))))

    finished = (int(sess.current_index or 0) >= total_items)

    # Recomendación simple
    total_answered = min(int(sess.current_index or 0), total_items)
    wrong = sum(1 for r in (sess.results[:total_answered] if sess.results else []) if r.get("correct") is False)
    recommended = None
    if total_answered >= 5 and (wrong / max(1, total_answered)) > 0.4:
        nxt = {"visual": "auditivo", "auditivo": "kinestesico", "kinestesico": "visual"}
        recommended = nxt.get(sess.style_used, "visual")
        ut = db.execute(
            select(UserTopic).where(UserTopic.user_id == me.id, UserTopic.topic_id == sess.topic_id)
        ).scalar_one_or_none()
        if ut and ut.recommended_style != recommended:
            ut.recommended_style = recommended
            db.add(ut)

    db.add(sess); db.commit(); db.refresh(sess)
    return {
        "correct": correct,
        "feedback": feedback,
        "nextIndex": int(sess.current_index or 0),
        "recommendedStyle": recommended,
        "finished": finished
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

    me_db2 = db.get(User, me.id)
    return {
        "ok": True,
        "awardedBadges": awarded,
        "points": int(me_db2.points or 0),
        "precisionPct": int(sess.score_pct or 0),     # 👈 útil para mostrar en el popup
        "errors": int(sess.mistakes_cnt or 0),
        "timeSec": int(sess.elapsed_sec or 0),
    }