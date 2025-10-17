from typing import Dict
from app.core.engines.grades.grade3.fracciones_basicas import FraccionesBasicasEngine

# Registry por slug de topic
_registry: Dict[str, object] = {
    "fracciones-basicas": FraccionesBasicasEngine(),
    # Aquí vas registrando nuevos temas:
    # "multiplicacion": MultiplicacionEngine(),
    # "numeros-primos": NumerosPrimosEngine(),
}

def get_engine_for_slug(slug: str):
    eng = _registry.get(slug)
    if not eng:
        raise KeyError(f"No hay engine registrado para slug={slug}")
    return eng