import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from app.db import Base, engine
from app.routers import auth as auth_router
from app.routers import user as user_router
from app.routers import verification as verification_router
from app.routers import password as password_router

load_dotenv()

app = FastAPI(title="EduMath API")

origins = os.getenv("CORS_ORIGINS", "").split(",") if os.getenv("CORS_ORIGINS") else ["http://localhost:4200"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# DEV: crea tablas si no existen (para producción usa Alembic)
Base.metadata.create_all(bind=engine)

app.include_router(auth_router.router)
app.include_router(verification_router.router)
app.include_router(password_router.router)
app.include_router(user_router.router)

@app.get("/health")
def health():
    return {"status": "ok"}
