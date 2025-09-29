from sqlalchemy import Column, Integer, ForeignKey, String, DateTime
from sqlalchemy.sql import func
from app.db import Base

class UserTopic(Base):
    __tablename__ = "user_topics"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    topic_id = Column(Integer, ForeignKey("topics.id", ondelete="CASCADE"), index=True, nullable=False)
    progress_pct = Column(Integer, nullable=False, default=0)      # 0..100
    recommended_style = Column(String(20), nullable=True)          # 'visual'|'auditivo'|'kinestesico'
    completed_count = Column(Integer, nullable=False, default=0)
    total_time_sec = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    attempts_total   = Column(Integer, nullable=False, default=0)  # suma de envíos de todas las sesiones
    errors_total     = Column(Integer, nullable=False, default=0)  # suma de incorrectas
    best_score_pct   = Column(Integer, nullable=False, default=0)  # mejor % en este tema
    last_score_pct   = Column(Integer, nullable=False, default=0)  # último %
    best_time_sec    = Column(Integer, nullable=True)              # mejor tiempo en segundos (menor es mejor)
    last_time_sec    = Column(Integer, nullable=False, default=0)  # último tiempo