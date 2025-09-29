from sqlalchemy import Column, Integer, ForeignKey, String, DateTime, JSON, Boolean
from sqlalchemy.sql import func
from app.db import Base

class TopicSession(Base):
    __tablename__ = "topic_sessions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    topic_id = Column(Integer, ForeignKey("topics.id", ondelete="CASCADE"), index=True, nullable=False)
    style_used = Column(String(20), nullable=False)         # estilo de esta sesión
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    elapsed_sec = Column(Integer, nullable=False, default=0)
    current_index = Column(Integer, nullable=False, default=0)     # 0..10
    items = Column(JSON, nullable=False, default=list)             # [{type,...,solution},...]
    results = Column(JSON, nullable=False, default=list)           # [{correct,attempts},...]
    points_awarded = Column(Boolean, nullable=False, default=False)
    score_raw     = Column(Integer, nullable=False, default=0)   # correctas (0..10)
    score_pct     = Column(Integer, nullable=False, default=0)   # 0..100
    mistakes_cnt  = Column(Integer, nullable=False, default=0)   # respuestas incorrectas en toda la sesión
    attempts_cnt  = Column(Integer, nullable=False, default=0)   # envíos totales (correctos + incorrectos)