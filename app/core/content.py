from pathlib import Path
from app.core.settings_static import APP_DIR, REPO_ROOT, CONTENT_DIR

def resolve_context_path(grade: int, slug: str) -> Path:
    p = CONTENT_DIR / f"grade-{grade}" / f"{slug}.json"
    if p.exists():
        return p
    fallback = REPO_ROOT / "content" / f"grade-{grade}" / f"{slug}.json"
    if fallback.exists():
        return fallback
    return p  # para que un 404 muestre la ruta esperada
