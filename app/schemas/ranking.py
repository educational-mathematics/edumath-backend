from pydantic import BaseModel
from typing import Optional

class RankingRow(BaseModel):
    rank: int
    alias: str
    points: int
    avatar_url: Optional[str] = None
