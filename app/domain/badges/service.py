from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from app.models.user import User
from app.models.badge import Badge
from app.models.user_badge import UserBadge

class BadgeNotFound(Exception): ...
class BadgeAlreadyOwned(Exception): ...

def award_by_slug(db: Session, user_id: int, slug: str) -> UserBadge:
    badge = db.execute(select(Badge).where(Badge.slug == slug)).scalar_one_or_none()
    if not badge:
        raise BadgeNotFound(slug)

    owned = db.execute(
        select(UserBadge.id).where(UserBadge.user_id == user_id, UserBadge.badge_id == badge.id)
    ).scalar_one_or_none()
    if owned:
        raise BadgeAlreadyOwned()

    ub = UserBadge(user_id=user_id, badge_id=badge.id)
    db.add(ub)
    db.commit()
    db.refresh(ub)
    return ub

def on_first_login_done(db: Session, user_id: int, *, old: bool, new: bool) -> bool:
    """Otorga 'welcome' SOLO si cambió de False -> True."""
    if (not old) and new:
        try:
            award_by_slug(db, user_id, "welcome")
            return True
        except (BadgeAlreadyOwned, BadgeNotFound):
            return False
    return False

def award_king_if_top1(db: Session, user_id: int, *, min_points: int = 1000) -> bool:
    """
    Otorga 'king' solo si el usuario es TOP 1 global Y tiene al menos min_points.
    """
    top = db.execute(
        select(User.id, User.points).order_by(desc(User.points), User.id.asc()).limit(1)
    ).first()  # (id, points) o None

    if top and top[0] == user_id and (top[1] or 0) >= min_points:
        try:
            award_by_slug(db, user_id, "king")
            return True
        except (BadgeAlreadyOwned, BadgeNotFound):
            return False
    return False