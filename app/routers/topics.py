# app/routers/topics.py
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from pathlib import Path
import json
import requests
from app.db import get_db
from app.deps import get_current_user
from app.models.user import User
from app.models.topic import Topic
from app.models.user_topic import UserTopic
from app.models.topic_session import TopicSession
from app.ai.gemini import generate_explanation, generate_exercises_variant, generate_one_image_png
from app.ai.variation_utils import extract_used_fractions

import os, logging, uuid
log = logging.getLogger("topics")

APP_DIR = Path(__file__).resolve().parents[1]   # .../app
REPO_ROOT = APP_DIR.parent                      # repo root (si app está en raíz)
CONTENT_DIR = Path(os.getenv("CONTENT_DIR", APP_DIR / "content"))

STATIC_GEN = Path(os.getenv("STATIC_GEN_DIR", Path(__file__).resolve().parents[1]/"static"/"generated"))
STATIC_GEN.mkdir(parents=True, exist_ok=True)

STATIC_DIR = (APP_DIR / "static").resolve()
TTS_DIR = (STATIC_DIR / "tts")
TTS_DIR.mkdir(parents=True, exist_ok=True)

def _make_tts(text: str, out_path: Path, voice: str = "Kore"):
    """
    Llama a tu endpoint interno /ai/tts para generar audio.
    Si falla, no levanta error (solo no genera audio).
    """
    try:
        r = requests.post(
            "http://localhost:8000/ai/tts",
            json={"text": text, "voice": voice},
            timeout=30
        )
        if r.status_code == 200:
            out_path.write_bytes(r.content)
    except Exception as e:
        print("[topics._make_tts] tts error:", e)

def _tts_url_for(session_id: int, name: str) -> str:
    # URL pública del archivo creado
    return f"/static/tts/sess-{session_id}-{name}.wav"

def _neutralize_audio_words(question: str) -> str:
    if not question: return question
    q = question.strip()
    # si empieza con "Escucha..." o "Imagina que te dictan..."
    lowers = q.lower()
    if lowers.startswith("escucha ") or "escucha atentamente" in lowers or "te dictan" in lowers:
        # cambia a lectura neutra
        return q.replace("Escucha atentamente ", "Lee atentamente ").replace("Escucha ", "Lee ").replace("te dictan", "se presentan")
    return q

def _save_png_return_url(topic_slug: str, png_bytes: bytes) -> str:
    name = f"{topic_slug}-{uuid.uuid4().hex[:8]}.png"
    out = STATIC_GEN / name
    out.write_bytes(png_bytes)
    # asumiendo que sirves /static desde FastAPI
    return f"/static/generated/{name}"

def resolve_context_path(grade: int, slug: str) -> Path:
    p = CONTENT_DIR / f"grade-{grade}" / f"{slug}.json"
    if p.exists():
        return p
    fallback = REPO_ROOT / "content" / f"grade-{grade}" / f"{slug}.json"
    if fallback.exists():
        return fallback
    return p  # devolver p (el principal) para que aparezca en el error

def _open_session_core(db: Session, me: User, ut: UserTopic, t: Topic):
    style = ut.recommended_style or (me.vak_style or "visual")

    # Contexto
    path = resolve_context_path(t.grade, t.slug)
    if not path.exists():
        log.warning("open_session: contexto no encontrado. grade=%s slug=%s path=%s", t.grade, t.slug, str(path))
        raise HTTPException(404, f"Contexto no encontrado: {path}")

    ctx = json.loads(path.read_text(encoding="utf-8"))
    _ = "\n".join(
        [c["text"] for c in ctx.get("concepts", [])] +
        [e["explain"] for e in ctx.get("examples", [])]
    )

    # última sesión del usuario en este topic
    last = db.execute(
        select(TopicSession)
        .where(TopicSession.user_id == me.id, TopicSession.topic_id == t.id)
        .order_by(TopicSession.id.desc())
    ).scalars().first()

    explanation: str | None = None
    need_new = (not last) or (last.current_index >= 10)

    if need_new:
        prev = db.execute(
            select(TopicSession)
            .where(TopicSession.user_id == me.id, TopicSession.topic_id == t.id)
            .order_by(TopicSession.id.desc()).limit(5)
        ).scalars().all()
        avoid_numbers: list[int] = []
        for s in prev:
            if s.items:
                avoid_numbers += extract_used_fractions(s.items)

        try:
            items = generate_exercises_variant(ctx, style=style, avoid_numbers=avoid_numbers)
            explanation = generate_explanation(ctx)
        except Exception as e:
            log.warning("open_session: IA falló, usando fallback. err=%s", e)
            from app.ai.gemini import fallback_generate_exercises, fallback_generate_explanation
            items = fallback_generate_exercises(ctx, style, avoid_numbers)
            explanation = fallback_generate_explanation(ctx)

        last = TopicSession(
            user_id=me.id, topic_id=t.id, style_used=style,
            items=items,
            results=[{"correct": None, "attempts": 0} for _ in range(10)],
            current_index=0,
            explanation=explanation  # guardar explicación
        )
        db.add(last); db.commit(); db.refresh(last)
    else:
        explanation = last.explanation  # reusar la que guardamos

    # ---------- Audio (siempre define la variable) ----------
    explanation_audio_url: str | None = None

    if style == "auditivo":
        # 1) Explicación -> WAV reutilizable
        exp_text = (last.explanation or explanation or "").strip()
        if exp_text:
            exp_path = (TTS_DIR / f"sess-{last.id}-explanation.wav")
            if not exp_path.exists():
                _make_tts(exp_text, exp_path, voice="Kore")
            if exp_path.exists():
                explanation_audio_url = _tts_url_for(last.id, "explanation")

        # 2) Audio para algunas preguntas + neutralizar lenguaje si no hay audio
        mcq_count = 0
        changed = False
        for idx, it in enumerate(last.items or []):
            if it.get("type") == "multiple_choice":
                needs_audio = False
                if mcq_count < 2:
                    needs_audio = True
                elif len((it.get("question") or "")) > 140:
                    needs_audio = True

                if needs_audio:
                    q_text = (it.get("question") or "").strip()
                    if q_text:
                        qpath = (TTS_DIR / f"sess-{last.id}-q{idx}.wav")
                        if not qpath.exists():
                            _make_tts(q_text, qpath, voice="Kore")
                        if qpath.exists():
                            it["ttsUrl"] = _tts_url_for(last.id, f"q{idx}")
                            mcq_count += 1
                            changed = True

            # Si no hay audio, cambia “Escucha…” -> “Lee…”
            if not it.get("ttsUrl"):
                q0 = it.get("question") or ""
                q1 = _neutralize_audio_words(q0)
                if q1 != q0:
                    it["question"] = q1
                    changed = True

        if changed:
            db.add(last); db.commit(); db.refresh(last)

    progress_in_session = min((last.current_index or 0) * 10, 100)

    return {
        "sessionId": last.id,
        "title": t.title,
        "style": last.style_used,
        "explanation": last.explanation or explanation,
        "explanationAudioUrl": explanation_audio_url,
        "currentIndex": last.current_index,
        "items": last.items,
        "progressInSession": progress_in_session,
    }

router = APIRouter(prefix="/topics", tags=["topics"])

# /topics/catalog  -> {3:[{id,slug,title,coverUrl}], 4:[...], ...}
@router.get("/catalog")
def catalog(db: Session = Depends(get_db)):
    rows = db.execute(select(Topic)).scalars().all()
    out: dict[int, list] = {}
    for t in rows:
        out.setdefault(int(t.grade), []).append({
            "id": t.id, "slug": t.slug, "title": t.title, "coverUrl": t.cover_url
        })
    return out

# /topics/my
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

# /topics/add/{topic_id}
@router.post("/add/{topic_id}")
def add_topic(topic_id: int, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    exists = db.execute(
        select(UserTopic.id).where(UserTopic.user_id==me.id, UserTopic.topic_id==topic_id)
    ).scalar_one_or_none()
    if exists:
        return {"ok": True, "userTopicId": exists}
    ut = UserTopic(user_id=me.id, topic_id=topic_id, progress_pct=0)
    db.add(ut); db.commit(); db.refresh(ut)
    return {"ok": True, "userTopicId": ut.id}

# /topics/{user_topic_id}/open
@router.post("/{user_topic_id}/open")
def open_session(user_topic_id: int, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    ut = db.get(UserTopic, user_topic_id)
    if not ut or ut.user_id != me.id:
        log.warning("open_session: user_topic no existe o no es del usuario. user_topic_id=%s me.id=%s", user_topic_id, me.id)
        raise HTTPException(404, "Tema no encontrado")

    t = db.get(Topic, ut.topic_id)
    if not t:
        raise HTTPException(404, "Topic asociado no existe")

    return _open_session_core(db, me, ut, t)
    
@router.post("/slug/{slug}/open")
def open_session_by_slug(slug: str, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    t = db.execute(select(Topic).where(Topic.slug == slug)).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Tema no encontrado")

    ut = db.execute(
        select(UserTopic).where(UserTopic.user_id == me.id, UserTopic.topic_id == t.id)
    ).scalar_one_or_none()
    if not ut:
        raise HTTPException(404, "Aún no añadiste este tema")

    return _open_session_core(db, me, ut, t)

# /topics/session/{session_id}/answer
@router.post("/session/{session_id}/answer")
def answer(session_id: int, body: dict, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    sess = db.get(TopicSession, session_id)
    if not sess or sess.user_id != me.id: raise HTTPException(404)
    idx = int(body.get("index", 0))
    answer = body.get("answer")
    if idx != sess.current_index: raise HTTPException(400, "Índice fuera de secuencia")
    item = sess.items[idx]

    def check(item, ans):
        t = item.get("type")
        if t == "multiple_choice":
            return int(ans) == int(item["correct_index"])
        if t == "match_pairs":
            # comparar como pares ordenados
            return ans == item.get("pairs")
        if t == "drag_to_bucket":
            return ans == item.get("solution")
        return False

    correct = check(item, answer)
    sess.results[idx]["attempts"] = int(sess.results[idx]["attempts"] or 0) + 1
    sess.results[idx]["correct"] = bool(correct)
    sess.attempts_cnt = int(sess.attempts_cnt or 0) + 1

    # recomendación simple si >40% fallos en últimas 5
    total = idx + 1
    wrong = sum(1 for r in sess.results[:total] if r["correct"] is False)
    recommended = None
    if total >= 5 and (wrong / max(1, total)) > 0.4:
        nxt = {"visual": "auditivo", "auditivo": "kinestesico", "kinestesico": "visual"}
        recommended = nxt.get(sess.style_used, "visual")
        ut = db.execute(select(UserTopic).where(UserTopic.user_id==me.id, UserTopic.topic_id==sess.topic_id)).scalar_one_or_none()
        if ut and ut.recommended_style != recommended:
            ut.recommended_style = recommended
            db.add(ut)

    feedback = None
    if not correct:
        feedback = item.get("explain", "Revisa el concepto clave y vuelve a intentar.")
        sess.mistakes_cnt = int(sess.mistakes_cnt or 0) + 1

    if correct:
        sess.current_index = min(sess.current_index + 1, 10)
        sess.score_raw = int(sess.score_raw or 0) + 1
        sess.score_pct = sess.score_raw * 10
        
        # ACTUALIZA PROGRESO DEL TEMA XD
        ut = db.execute(
            select(UserTopic).where(
                UserTopic.user_id == me.id,
                UserTopic.topic_id == sess.topic_id
            )
        ).scalar_one_or_none()
        if ut:
            pct = min(sess.current_index * 10, 100)
            if int(ut.progress_pct or 0) < pct:
                ut.progress_pct = pct
                db.add(ut)

    db.add(sess); db.commit(); db.refresh(sess)
    return {"correct": correct, "feedback": feedback, "nextIndex": sess.current_index, "recommendedStyle": recommended}

# /topics/session/{session_id}/finish
from app.domain.badges.service import on_points_changed
from app.models.badge import Badge
from app.models.user_badge import UserBadge

@router.post("/session/{session_id}/finish")
def finish(session_id: int, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    sess = db.get(TopicSession, session_id)
    if not sess or sess.user_id != me.id:
        raise HTTPException(404)
    if sess.current_index < 10:
        raise HTTPException(400, "Sesión incompleta")

    # (si envías elapsed desde front, súmalo aquí antes)
    sess.elapsed_sec = int(sess.elapsed_sec or 0)
    sess.ended_at = func.now()

    ut = db.execute(select(UserTopic).where(UserTopic.user_id==me.id, UserTopic.topic_id==sess.topic_id)).scalar_one()
    ut.progress_pct = 100
    ut.completed_count = int(ut.completed_count or 0) + 1
    ut.attempts_total  = int(ut.attempts_total or 0) + int(sess.attempts_cnt or 0)
    ut.errors_total    = int(ut.errors_total   or 0) + int(sess.mistakes_cnt or 0)
    ut.last_score_pct  = int(sess.score_pct or 0)
    ut.last_time_sec   = int(sess.elapsed_sec or 0)
    
    if ut.best_score_pct is None or int(sess.score_pct or 0) > int(ut.best_score_pct or 0):
        ut.best_score_pct = int(sess.score_pct or 0)
        
    if sess.elapsed_sec and (ut.best_time_sec is None or int(sess.elapsed_sec) < int(ut.best_time_sec)):
        ut.best_time_sec = int(sess.elapsed_sec)
    
    db.add(ut)

    awarded = []
    if not sess.points_awarded:
        old = int(me.points or 0)
        bonus = 0
        # ejemplo opcional: +10 por perfecto
        if (sess.score_pct or 0) == 100:
            bonus += 10
            
        new = old + 100 + bonus
        me.points = new
        db.add(me); db.commit()
        slugs = on_points_changed(db, me.id, old_points=old, new_points=new)
        if slugs:
            rows = db.execute(select(Badge).where(Badge.slug.in_(slugs))).scalars().all()
            for b in rows:
                owners = db.execute(select(func.count(func.distinct(UserBadge.user_id))).where(UserBadge.badge_id==b.id)).scalar_one() or 0
                total_users = db.execute(select(func.count(User.id))).scalar_one() or 1
                rarity = round(owners * 100.0 / total_users, 2)
                owned = True
                awarded.append({"id": b.id, "slug": b.slug, "title": b.title, "imageUrl": b.image_url, "rarityPct": rarity, "owned": owned})
        ##slugs = on_points_changed(db, me.id, old_points=old, new_points=new)
        sess.points_awarded = True
        db.add(sess)

    db.commit()
    return {"ok": True, "awardedBadges": awarded, "points": me.points}