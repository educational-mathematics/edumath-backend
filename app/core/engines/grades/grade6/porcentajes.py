from __future__ import annotations
from pathlib import Path
import json
from typing import Any, Dict, List, Literal, Optional

from app.core.engines.base import TopicEngine
from app.core.content import resolve_context_path
from app.ai.gemini import generate_explanation, generate_exercises_variant

VAK = Literal["visual","auditivo","kinestesico"]

class PorcentajesEngine(TopicEngine):
    slug  = "porcentajes"
    grade = 6
    title = "Porcentajes"

    # === API de construcción de sesión (llamada por /topics/slug/.../open) ===
    def build_session(
        self,
        *,
        context_json: Dict[str, Any],
        style: VAK,
        avoid_numbers: Optional[List[int]] = None,
        reuse_mode: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Debe devolver:
          {
            "explanation": str,
            "items": list[dict],        # 10 ejercicios saneados
            "style_meta": {"style": "visual|auditivo|kinestesico"},
            "assets": {"images": [...], "audio_urls": [...]}
          }
        """
        # Usa el contexto que ya te entrega el core (no vuelvas a leer disco aquí).
        ctx = context_json or {"grade": self.grade, "slug": self.slug, "title": self.title}

        # Explicación corta inicial (texto). El core se encarga de TTS/imagenes si aplica en TopicPlay.
        explanation = generate_explanation(ctx)

        # Ítems según estilo (visual/auditivo generan MCQ/pareo/drag; kinestésico evita MCQ).
        items = generate_exercises_variant(ctx, style, avoid_numbers=avoid_numbers or [])

        # Meta y assets iniciales
        style_meta = {"style": style}
        assets = {"images": [], "audio_urls": []}

        return {
            "explanation": explanation,
            "items": items,
            "style_meta": style_meta,
            "assets": assets,
        }

    # === Helpers opcionales usados por otras rutas ===
    def load_context(self) -> Dict[str, Any]:
        p: Path = resolve_context_path(self.grade, self.slug)
        if not p.exists():
            return {"grade": self.grade, "slug": self.slug, "title": self.title,
                    "concepts": [], "examples": [], "constraints": {}}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {"grade": self.grade, "slug": self.slug, "title": self.title}

    # Para la explicación corta en TopicPlay (si el core la llama directo)
    def generate_initial_explanation(self, style: VAK) -> str:
        ctx = self.load_context()
        return generate_explanation(ctx)

    # Para generar/variar ejercicios fuera del build_session si el core lo requiere
    def generate_exercises(self, style: VAK, avoid_numbers: Optional[List[int]] = None) -> List[Dict[str, Any]]:
        ctx = self.load_context()
        return generate_exercises_variant(ctx, style, avoid_numbers=avoid_numbers or [])