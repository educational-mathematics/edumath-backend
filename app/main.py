import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from fastapi.staticfiles import StaticFiles

from app.db import Base, engine

from app.routers import auth as auth_router
from app.routers import user as user_router
from app.routers import verification as verification_router
from app.routers import password as password_router
from app.routers import ranking as ranking_router
from app.routers import badges as badges_router
from app.routers import points as points_router
from app.routers import me as me_router
from app.routers import topics as topics_router
from app.routers import tts

# <-- /static (dentro de app) ya configurado en settings_static
from app.core.settings_static import STATIC_DIR, MEDIA_DIR  # app/static

load_dotenv()
Base.metadata.create_all(bind=engine)

app = FastAPI(title="EduMath API")

# ==== Directorios estáticos ====
# A) /static -> app/static  (tts, gen, etc.)
APP_STATIC_DIR = STATIC_DIR.resolve()

# B) /media  -> <repo_root>/static  (covers, avatars, badges)
APP_DIR = Path(__file__).resolve().parent          # app/
REPO_ROOT = APP_DIR.parent                         # <repo_root>/
PUBLIC_MEDIA_DIR = (REPO_ROOT / "static").resolve()

# Asegurar subcarpetas típicas en /media
(PUBLIC_MEDIA_DIR / "covers").mkdir(parents=True, exist_ok=True)
(PUBLIC_MEDIA_DIR / "avatars").mkdir(parents=True, exist_ok=True)
(PUBLIC_MEDIA_DIR / "badges").mkdir(parents=True, exist_ok=True)

# Montajes
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")  # app/static → TTS, generados
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media") # static/ (raíz) → covers, avatars, badges

# ==== CORS ====
origins = os.getenv("CORS_ORIGINS", "")
origins_list = [o.strip() for o in origins.split(",")] if origins else ["http://localhost:4200"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if os.getenv("DEV_AUTO_CREATE", "0") == "1":
    Base.metadata.create_all(bind=engine)

# ==== Routers ====
app.include_router(auth_router.router)
app.include_router(verification_router.router)
app.include_router(password_router.router)
app.include_router(user_router.router)
app.include_router(ranking_router.router)
app.include_router(badges_router.router)
app.include_router(me_router.router)
app.include_router(points_router.router)
app.include_router(topics_router.router)
app.include_router(tts.router)

@app.get("/health")
def health():
    return {"status": "ok"}
