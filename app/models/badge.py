from sqlalchemy import Column, Integer, String, Text, UniqueConstraint
from app.db import Base

class Badge(Base):
    __tablename__ = "badges"
    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(80), unique=True, index=True, nullable=False)
    title = Column(String(120), nullable=False)
    description = Column(Text, nullable=False, default="")
    image_url = Column(String(255), nullable=False)
    __table_args__ = (UniqueConstraint('slug', name='uq_badges_slug'),)
