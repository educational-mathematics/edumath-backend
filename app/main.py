import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from app.db import Base, engine
from app.routers import auth as auth_router
from app.routers import user as user_router
from app.routers import verification as verification_router
from app.routers import password as password_router
from app.routers import ranking as ranking_router
from app.routers import ranking as ranking_router
from fastapi.staticfiles import StaticFiles
from app.routers import badges as badges_router
from app.routers import points as points_router

from app.models import badge  
from app.models import user_badge 
from app.routers import me as me_router

from app.routers import topics as topics_router
from app.routers import tts

load_dotenv()
Base.metadata.create_all(bind=engine)
app = FastAPI(title="EduMath API")

# Crea carpetas si no existen
os.makedirs("static/avatars", exist_ok=True)

# Monta /static para servir archivos
app.mount("/static", StaticFiles(directory="static"), name="static")

origins = os.getenv("CORS_ORIGINS", "").split(",") if os.getenv("CORS_ORIGINS") else ["http://localhost:4200"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ⚠️ Solo en DEV si quieres crear tablas rápido (en prod usa Alembic)
if os.getenv("DEV_AUTO_CREATE", "0") == "1":
    Base.metadata.create_all(bind=engine)

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

#ayuda