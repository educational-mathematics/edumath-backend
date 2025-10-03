from pathlib import Path
import os

# Raíz del paquete app/  ->  .../app
APP_DIR = Path(__file__).resolve().parents[1]
# Raíz del repo (padre de app/)  ->  ...
REPO_ROOT = APP_DIR.parent

# === Contenido (JSON) de los temas ===
# Puedes sobreescribir con la var de entorno CONTENT_DIR si quieres
CONTENT_DIR = Path(os.getenv("CONTENT_DIR", REPO_ROOT / "content")).resolve()
CONTENT_DIR.mkdir(parents=True, exist_ok=True)

# === MEDIA global, FUERA de app ===
# Aquí viven covers/, avatars/, badges/
MEDIA_DIR = (REPO_ROOT / "static").resolve()
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# === STATIC interno de app (archivos generados por la app) ===
# Aquí van tts/, gen/, generated/
STATIC_DIR = (APP_DIR / "static").resolve()
STATIC_DIR.mkdir(parents=True, exist_ok=True)

TTS_DIR = STATIC_DIR / "tts";        TTS_DIR.mkdir(parents=True, exist_ok=True)
GEN_DIR = STATIC_DIR / "gen";        GEN_DIR.mkdir(parents=True, exist_ok=True)
STATIC_GENERATED_DIR = STATIC_DIR / "generated"; STATIC_GENERATED_DIR.mkdir(parents=True, exist_ok=True)

def media_url_for(abs_path: Path) -> str:
    """Devuelve /media/... para un Path dentro de MEDIA_DIR."""
    rel = abs_path.resolve().relative_to(MEDIA_DIR)
    return f"/media/{rel.as_posix()}"

def static_url_for(abs_path: Path) -> str:
    """Devuelve /static/... para un Path dentro de STATIC_DIR."""
    rel = abs_path.resolve().relative_to(STATIC_DIR)
    return f"/static/{rel.as_posix()}"