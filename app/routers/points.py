from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session
from sqlalchemy import func, select
from app.deps import get_db, get_current_user
from app.models.user import User
from app.models.badge import Badge
from app.models.user_badge import UserBadge
from app.domain.badges.service import on_points_changed

router = APIRouter(prefix="/users/me", tags=["me"])

class PointsChangeIn(BaseModel):
    op: str  # "add" | "set"
    value: int

    @field_validator("op")
    def valid_op(cls, v):
        if v not in {"add", "set"}:
            raise ValueError("op must be 'add' or 'set'")
        return v

class BadgeOut(BaseModel):
    id: int
    slug: str
    title: str
    description: str
    imageUrl: str
    rarityPct: float
    owned: bool

@router.post("/points")
def change_points(
    body: PointsChangeIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user)
):
    old = int(me.points or 0)
    new = old + body.value if body.op == "add" else body.value
    if new < 0:
        new = 0  # defensivo; ajusta a tu negocio

    if new == old:
        return {"ok": True, "points": new, "awardedBadges": []}

    me.points = new
    db.add(me)
    db.commit()
    db.refresh(me)

    slugs = on_points_changed(db, me.id, old_points=old, new_points=new)
    if not slugs:
        return {"ok": True, "points": new, "awardedBadges": []}

    # Armar BadgeOut (con owned/rarityPct) igual que /badges
    total_users = db.execute(select(func.count(User.id))).scalar_one() or 1

    # Obtener badges por slugs
    rows = db.execute(select(Badge).where(Badge.slug.in_(slugs))).scalars().all()
    out: list[dict] = []
    for b in rows:
        owners = db.execute(
            select(func.count(func.distinct(UserBadge.user_id))).where(UserBadge.badge_id == b.id)
        ).scalar_one() or 0
        rarity = round(owners * 100.0 / total_users, 2)
        owned = db.execute(
            select(func.count()).where(UserBadge.user_id == me.id, UserBadge.badge_id == b.id)
        ).scalar_one() > 0
        out.append({
            "id": b.id,
            "slug": b.slug,
            "title": b.title,
            "description": b.description,
            "imageUrl": b.image_url,
            "rarityPct": rarity,
            "owned": owned,
        })

    return {"ok": True, "points": new, "awardedBadges": out}
