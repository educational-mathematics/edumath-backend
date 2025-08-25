from sqlalchemy import Column, Integer, String, Boolean, DateTime, JSON
from sqlalchemy.sql import func
from app.db import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password = Column(String(255), nullable=False)

    first_login_done = Column(Boolean, default=False)

    vak_style = Column(String(20), nullable=True)  # visual | auditivo | kinestesico
    vak_scores = Column(JSON, nullable=True)       # {"visual":13,"auditivo":12,"kinestesico":19}

    test_answered_by = Column(String(20), nullable=True)  # alumno | representante
    test_date = Column(DateTime(timezone=True), server_default=func.now())
    
    email_verified = Column(Boolean, default=False, nullable=False)
    avatar_url = Column(String(512), nullable=True)