from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.user import User
from app.domain.badges.service import on_first_login_done
from sqlalchemy import select, func
from app.models.badge import Badge
from app.models.user_badge import UserBadge

router = APIRouter(prefix="/users/me", tags=["me"])

@router.post("/first-login-done")
def mark_first_login_done(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user)
):
    old = bool(me.first_login_done)
    if not me.first_login_done:
        me.first_login_done = True
        db.add(me)
        db.commit()
        db.refresh(me)

    awarded = on_first_login_done(db, me.id, old=old, new=True)
    #return {"ok": True, "awardedWelcome": awarded}
    awardedBadges = []
    if awarded:
        total_users = db.execute(select(func.count(User.id))).scalar_one() or 1
        b = db.execute(select(Badge).where(Badge.slug == "welcome")).scalar_one_or_none()
        if b:
            owners = db.execute(
                select(func.count(func.distinct(UserBadge.user_id))).where(UserBadge.badge_id == b.id)
            ).scalar_one() or 0
            rarity = round(owners * 100.0 / total_users, 2)
            owned = db.execute(
                select(func.count()).where(UserBadge.user_id == me.id, UserBadge.badge_id == b.id)
            ).scalar_one() > 0
            awardedBadges.append({
                "id": b.id,
                "slug": b.slug,
                "title": b.title,
                "description": b.description,
                "imageUrl": b.image_url,
                "rarityPct": rarity,
                "owned": owned,
            })

    return {"ok": True, "awardedBadges": awardedBadges}
