from sqlalchemy import Column, Integer, String
from app.db import Base

class Topic(Base):
    __tablename__ = "topics"
    id = Column(Integer, primary_key=True)
    grade = Column(Integer, nullable=False)                 # 3..6
    slug = Column(String(80), unique=True, index=True, nullable=False)
    title = Column(String(160), nullable=False)
    cover_url = Column(String(512), nullable=True)          # portada catálogo