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
            award_by_slug(db, user_id, "rey")
            return True
        except (BadgeAlreadyOwned, BadgeNotFound):
            return False
    return False

def on_points_changed(db: Session, user_id: int, old_points: int, new_points: int) -> list[str]:
    """
    Devuelve slugs recién otorgados. Idempotente.
    Reglas: umbrales y 'king' si aplica.
    """
    awarded: list[str] = []

    def try_award(slug: str):
        nonlocal awarded
        try:
            award_by_slug(db, user_id, slug)
            awarded.append(slug)
        except (BadgeAlreadyOwned, BadgeNotFound):
            pass

    # Umbrales
    if old_points < 1000 <= new_points:
        try_award("principiante-elite")
    if old_points < 10000 <= new_points:
        try_award("estrella-platinada")
    if old_points < 1_000_000 <= new_points:
        try_award("leyenda-viva")

    # King (Top 1 y >= 1000)
    if award_king_if_top1(db, user_id, min_points=1000):
        awarded.append("rey")

    return awarded