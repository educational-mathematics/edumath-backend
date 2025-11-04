from sqlalchemy import Column, Integer, ForeignKey, DateTime, UniqueConstraint, func
from app.db import Base

class UserBadge(Base):
    __tablename__ = "user_badges"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    badge_id = Column(Integer, ForeignKey("badges.id", ondelete="CASCADE"), index=True, nullable=False)
    obtained_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint('user_id', 'badge_id', name='uq_user_badge'),)
