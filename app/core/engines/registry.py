from __future__ import annotations
from typing import Optional, Tuple, Dict, Type
from app.core.engines.base import TopicEngine
from app.core.engines.grades.grade3.fracciones_basicas import FraccionesBasicasEngine

_ENGINE_MAP: Dict[Tuple[int, str], Type[TopicEngine]] = {
    (3, "fracciones-basicas"): FraccionesBasicasEngine,
}

def _normalize_args(a, b=None) -> Tuple[Optional[int], str]:
    """
    Acepta (slug, grade) o (grade, slug) y devuelve (grade:int|None, slug:str).
    """
    if b is None:
        # solo slug
        return None, str(a)
    # intenta detectar tipos
    if isinstance(a, int) and isinstance(b, str):
        return a, b
    if isinstance(a, str) and isinstance(b, int):
        return b, a
    # si ambos son str, el segundo debería ser slug
    if isinstance(a, str) and isinstance(b, str):
        # asume (slug, grade_str?) o (grade_str?, slug)
        try:
            g = int(a)
            return g, b
        except Exception:
            try:
                g = int(b)
                return g, a
            except Exception:
                # sin grade legible -> busca solo por slug
                return None, a
    # fallback
    try:
        return int(a), str(b)
    except Exception:
        return None, str(a)

def get_engine_for_slug(arg1, arg2=None) -> TopicEngine:
    """
    Uso:
      get_engine_for_slug(slug)
      get_engine_for_slug(slug, grade)
      get_engine_for_slug(grade, slug)
    """
    grade, slug = _normalize_args(arg1, arg2)
    # primero intenta con (grade, slug) si lo tenemos
    if grade is not None:
        cls = _ENGINE_MAP.get((int(grade), slug))
        if cls:
            return cls()
    # luego intenta por slug ignorando grade
    for (g, s), cls in _ENGINE_MAP.items():
        if s == slug:
            return cls()
    raise ValueError(f"Engine no encontrado para slug={slug}, grade={grade}")
