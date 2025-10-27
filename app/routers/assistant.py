from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select, and_, func
from uuid import uuid4
import threading, json, os, logging
from pathlib import Path
from typing import Literal

from app.db import get_db
from app.deps import get_current_user
from app.models.user import User
from app.models.topic import Topic
from app.models.assistant_explanation import AssistantExplanation

# Helpers existentes
from app.core.content import resolve_context_path
from app.ai.gemini import generate_explanation, generate_one_image_png
from app.core.settings_static import STATIC_DIR
from app.core.utils_tts import make_tts

log = logging.getLogger("assistant")
router = APIRouter(prefix="/assistant", tags=["assistant"])

VakStyle = Literal["visual","auditivo"]

# ---------- Utiles de archivo ----------
TTS_SUBDIR = STATIC_DIR / "tts"
IMG_SUBDIR = STATIC_DIR / "gen"

def _wav_url_for(expl_id: str, pid: str) -> str:
    # /static/tts/assist-<expl>-<pid>.wav
    name = f"assist-{expl_id}-{pid}.wav"
    return f"/static/tts/{name}"

def _wav_path_for(expl_id: str, pid: str) -> Path:
    TTS_SUBDIR.mkdir(parents=True, exist_ok=True)
    return TTS_SUBDIR / f"assist-{expl_id}-{pid}.wav"

def _png_url_for(expl_id: str, pid: str) -> str:
    # /static/gen/assist-<expl>-<pid>.png
    name = f"assist-{expl_id}-{pid}.png"
    return f"/static/gen/{name}"

def _png_path_for(expl_id: str, pid: str) -> Path:
    IMG_SUBDIR.mkdir(parents=True, exist_ok=True)
    return IMG_SUBDIR / f"assist-{expl_id}-{pid}.png"

def _split_paragraphs(text: str) -> list[str]:
    raw = (text or "").replace("\r\n", "\n")
    parts = [p.strip() for p in raw.split("\n\n") if p.strip()]
    # evita párrafos gigantes
    fixed: list[str] = []
    for p in parts:
        if len(p) > 650:
            # corte blando por punto
            chunks = []
            cur = ""
            for sent in p.split(". "):
                cur = (cur + (" " if cur else "") + sent).strip()
                if len(cur) >= 450:
                    chunks.append(cur + ".")
                    cur = ""
            if cur:
                chunks.append(cur if cur.endswith(".") else cur + ".")
            fixed += chunks
        else:
            fixed.append(p if p.endswith(".") else p + ".")
    return fixed[:12]  # límite sano

# ---------- Endpoints ----------

@router.get("/topics")
def get_topics(db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    """
    Devuelve topics disponibles para generar explicación (excluye los ya presentes en historial).
    """
    # topics con alguna explicación del usuario
    explained_topic_ids = [tid for (tid,) in db.execute(
        select(AssistantExplanation.topic_id).where(AssistantExplanation.user_id == me.id).distinct()
    ).all()]
    explained_topic_ids = set(int(x) for x in explained_topic_ids)

    rows = db.execute(select(Topic)).scalars().all()
    out = []
    for t in rows:
        if t.id in explained_topic_ids:
            # omitir del selector (como pediste)
            continue
        out.append({
            "id": t.id,
            "grade": int(t.grade),
            "slug": t.slug,
            "title": t.title
        })
    return out

@router.get("/history")
def history(db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    """
    Devuelve historial agrupado por grado/tema, con la explicación más reciente por estilo.
    """
    rows = db.execute(
        select(AssistantExplanation, Topic)
        .where(AssistantExplanation.user_id == me.id)
        .join(Topic, Topic.id == AssistantExplanation.topic_id)
        .order_by(AssistantExplanation.created_at.desc())
    ).all()

    # mapa (grade -> topicId -> {topicTitle, visual, auditivo})
    bucket: dict[int, dict[int, dict]] = {}
    for expl, t in rows:
        g = int(t.grade)
        bucket.setdefault(g, {})
        bucket[g].setdefault(t.id, {"topicId": t.id, "topicTitle": t.title})
        key = expl.style
        # solo guarda la más reciente por estilo
        if key not in bucket[g][t.id]:
            payload = expl.payload or {}
            bucket[g][t.id][key] = {
                "id": expl.id,
                "topicId": t.id,
                "topicTitle": t.title,
                "grade": g,
                "style": expl.style,
                "status": expl.status,
                "createdAt": expl.created_at.isoformat() if expl.created_at else None,
                "paragraphs": payload.get("paragraphs") or [],
                "notes": expl.notes or None,
            }

    out = []
    for grade in sorted(bucket.keys()):
        topics = list(bucket[grade].values())
        out.append({"grade": grade, "topics": topics})
    return out

@router.post("/explanations/start")
def start_explanation(body: dict, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    topic_id = int(body.get("topicId") or 0)
    style: VakStyle = (body.get("style") or "visual").lower()
    if style not in ("visual","auditivo"):
        raise HTTPException(400, "style inválido")

    t = db.get(Topic, topic_id)
    if not t:
        raise HTTPException(404, "Tema no encontrado")

    expl_id = uuid4().hex[:16]
    rec = AssistantExplanation(
        id=expl_id,
        user_id=me.id,
        topic_id=t.id,
        grade=int(t.grade),
        style=style,
        status="in_progress",
        notes=None,
        payload={"topicTitle": t.title, "paragraphs": []}
    )
    db.add(rec); db.commit()

    # dispara generación en background
    threading.Thread(target=_worker_generate, args=(expl_id,), daemon=True).start()

    return {"explanationId": expl_id}

@router.get("/explanations/{expl_id}")
def get_explanation(expl_id: str, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    rec = db.get(AssistantExplanation, expl_id)
    if not rec or rec.user_id != me.id:
        raise HTTPException(404)
    payload = rec.payload or {}
    return {
        "id": rec.id,
        "topicId": rec.topic_id,
        "topicTitle": payload.get("topicTitle"),
        "grade": rec.grade,
        "style": rec.style,
        "status": rec.status,
        "notes": rec.notes,
        "createdAt": rec.created_at.isoformat() if rec.created_at else None,
        "paragraphs": payload.get("paragraphs") or []
    }

@router.post("/explanations/{expl_id}/resume")
def resume_explanation(expl_id: str, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    rec = db.get(AssistantExplanation, expl_id)
    if not rec or rec.user_id != me.id:
        raise HTTPException(404)
    if rec.status not in ("interrupted","failed"):
        return {"ok": True}  # nada que hacer
    rec.status = "in_progress"
    rec.notes = None
    db.add(rec); db.commit()
    threading.Thread(target=_worker_generate, args=(expl_id,), daemon=True).start()
    return {"ok": True}

# ---------- Worker principal ----------

def _worker_generate(expl_id: str):
    """Genera la explicación (visual o auditivo) en background."""
    from app.db import SessionLocal  # evita ciclos
    db: Session = SessionLocal()
    try:
        rec: AssistantExplanation | None = db.get(AssistantExplanation, expl_id)
        if not rec:
            return
        user_id = rec.user_id
        topic: Topic = db.get(Topic, rec.topic_id)

        # Carga contexto (si existe JSON contextual de tu tema)
        ctx_path = resolve_context_path(topic.grade, topic.slug)
        ctx = {}
        if ctx_path.exists():
            try:
                ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
            except Exception:
                ctx = {}

        # Texto base con IA (si no existe ya en otro estilo)
        text = ""
        if rec.style == "auditivo":
            # intenta reciclar el texto visual previo (si completed)
            prev_visual = db.execute(
                select(AssistantExplanation)
                .where(and_(
                    AssistantExplanation.user_id == user_id,
                    AssistantExplanation.topic_id == topic.id,
                    AssistantExplanation.style == "visual",
                    AssistantExplanation.status == "completed"
                ))
                .order_by(AssistantExplanation.created_at.desc())
                .limit(1)
            ).scalars().first()
            if prev_visual and (prev_visual.payload or {}).get("paragraphs"):
                text = "\n\n".join(p.get("text","") for p in prev_visual.payload["paragraphs"])

        if not text:
            text = generate_explanation(ctx) or (topic.title + ": explicación.")

        paragraphs_txt = _split_paragraphs(text)
        payload = rec.payload or {"topicTitle": topic.title}
        payload["paragraphs"] = payload.get("paragraphs") or []

        # Si ya había párrafos (reintento), continúa desde el siguiente vacío
        existing_ids = {p.get("id") for p in payload["paragraphs"]}
        idx_start = len(existing_ids) if existing_ids else 0

        for i, ptxt in enumerate(paragraphs_txt[idx_start:], start=idx_start):
            pid = f"p{i+1}"
            row = {"id": pid, "text": ptxt}

            if rec.style == "visual":
                # Imagen opcional ligera: una sola para p1, o intenta mini-fig para algunas
                try:
                    if i == 0:
                        png = generate_one_image_png(ptxt[:140])
                        if png:
                            out = _png_path_for(expl_id, pid)
                            out.write_bytes(png)
                            row["imageUrl"] = _png_url_for(expl_id, pid)
                except Exception as e:
                    log.warning("visual img gen fail: %s", e)

            else:  # auditivo -> TTS por párrafo
                try:
                    wav_path = _wav_path_for(expl_id, pid)
                    make_tts(ptxt, wav_path, voice=os.getenv("TTS_VOICE","es-ES-Neural2-A"))
                    if wav_path.exists():
                        row["audioUrl"] = _wav_url_for(expl_id, pid)
                except Exception as e:
                    log.warning("tts per-paragraph fail: %s", e)

            payload["paragraphs"].append(row)
            rec.payload = payload
            db.add(rec); db.commit()

        rec.status = "completed"
        rec.notes = None
        rec.payload = payload
        db.add(rec); db.commit()

    except Exception as e:
        log.exception("assistant worker failed: %s", e)
        try:
            rec = db.get(AssistantExplanation, expl_id)
            if rec:
                rec.status = "interrupted"
                rec.notes = str(e)
                db.add(rec); db.commit()
        except Exception:
            pass
    finally:
        db.close()
