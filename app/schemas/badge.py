from pydantic import BaseModel

class BadgeOut(BaseModel):
    id: int
    slug: str
    title: str
    description: str
    imageUrl: str
    rarityPct: float
    owned: bool

    class Config:
        from_attributes = True  # pydantic v2
