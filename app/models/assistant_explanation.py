from __future__ import annotations
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db import Base

class AssistantExplanation(Base):
    __tablename__ = "assistant_explanations"

    id          = Column(String(64), primary_key=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    topic_id    = Column(Integer, ForeignKey("topics.id"), nullable=False)
    grade       = Column(Integer, nullable=False)
    style       = Column(String(16), nullable=False)   # "visual" | "auditivo"
    status      = Column(String(16), nullable=False)   # "in_progress" | "completed" | "interrupted" | "failed"
    notes       = Column(Text, nullable=True)
    payload     = Column(JSON, nullable=True)          # { paragraphs:[{id,text,imageUrl?,audioUrl?}], topicTitle }
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # relaciones opcionales
    topic = relationship("Topic", lazy="joined")