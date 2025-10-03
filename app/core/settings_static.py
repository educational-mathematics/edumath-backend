from pathlib import Path
import os

# === Paths base ===
APP_DIR   = Path(__file__).resolve().parents[1]   # carpeta app/
REPO_ROOT = APP_DIR.parent                        # raíz del repo

# === Contenido (JSON de temas) ===
CONTENT_DIR = Path(os.getenv("CONTENT_DIR", APP_DIR / "content")).resolve()

# === STATIC (generado por la app: tts, imágenes de ejercicios, explicaciones) ===
STATIC_DIR = (APP_DIR / "static").resolve()
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# Subcarpetas
TTS_DIR = STATIC_DIR / "tts"
TTS_DIR.mkdir(parents=True, exist_ok=True)

STATIC_GENERATED_DIR = STATIC_DIR / "generated"
STATIC_GENERATED_DIR.mkdir(parents=True, exist_ok=True)

GEN_DIR = STATIC_DIR / "gen"
GEN_DIR.mkdir(parents=True, exist_ok=True)

# === MEDIA (archivos editoriales subidos o fijos: covers, avatars, badges) ===
MEDIA_DIR = Path(__file__).resolve().parents[1] / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
(MEDIA_DIR / "covers").mkdir(parents=True, exist_ok=True)
(MEDIA_DIR / "avatars").mkdir(parents=True, exist_ok=True)
(MEDIA_DIR / "badges").mkdir(parents=True, exist_ok=True)

# Helpers
def static_url_for(abs_path: Path) -> str:
    """Devuelve /static/... para un Path dentro de STATIC_DIR (app/static)."""
    rel = abs_path.resolve().relative_to(STATIC_DIR)
    return f"/static/{rel.as_posix()}"

def media_url_for(abs_path: Path) -> str:
    """Devuelve /media/... para un Path dentro de MEDIA_DIR (raíz/static)."""
    rel = abs_path.resolve().relative_to(MEDIA_DIR)
    return f"/media/{rel.as_posix()}"
