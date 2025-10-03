from fastapi import APIRouter, Depends
from sqlalchemy import func, select, exists, cast, Boolean
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.user import User
from app.models.badge import Badge
from app.models.user_badge import UserBadge
from app.schemas.badge import BadgeOut
from app.deps import get_current_user  # ajusta a tu proyecto

router = APIRouter(prefix="/badges", tags=["badges"])

def _norm_media_url(raw: str | None) -> str | None:
    if not raw:
        return None
    u = raw.strip()
    if u.startswith("http://") or u.startswith("https://"):
        return u
    if u.startswith("/media/"):
        return u
    if u.startswith("/static/"):
        # convertir viejo a nuevo
        return "/media/" + u.lstrip("/static/").lstrip("/")
    if u.startswith(("badges/", "avatars/", "covers/")):
        return "/media/" + u
    # fallback
    return "/media/" + u.lstrip("/")

@router.get("", response_model=list[BadgeOut])
def list_badges(db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    total_users = db.execute(select(func.count(User.id))).scalar_one() or 1

    # Conteo de dueños por insignia
    ub_counts = (
        select(
            UserBadge.badge_id,
            func.count(func.distinct(UserBadge.user_id)).label("owners")
        )
        .group_by(UserBadge.badge_id)
        .subquery()
    )

    # owned: ¿el usuario actual posee esta badge?
    owned_expr = cast(
        exists(
            select(UserBadge.id)
            .where(
                UserBadge.user_id == me.id,
                UserBadge.badge_id == Badge.id
            )
        ).correlate(Badge),  # correlaciona con la tabla principal
        Boolean
    ).label("owned")

    q = (
        select(
            Badge.id,
            Badge.slug,
            Badge.title,
            Badge.description,
            Badge.image_url.label("imageUrl"),
            (
                (func.coalesce(ub_counts.c.owners, 0) * 100.0) / total_users
            ).label("rarityPct"),
            owned_expr,
        )
        .join(ub_counts, ub_counts.c.badge_id == Badge.id, isouter=True)
    )

    rows = db.execute(q).mappings().all()
    out = []
    for r in rows:
        r = dict(r)
        r["rarityPct"] = round(float(r["rarityPct"] or 0.0), 2)
        out.append(r)
    return out

@router.get("/{badge_id}", response_model=BadgeOut)
def get_badge(badge_id: int, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    total_users = db.execute(select(func.count(User.id))).scalar_one() or 1
    owners = db.execute(
        select(func.count(func.distinct(UserBadge.user_id))).where(UserBadge.badge_id == badge_id)
    ).scalar_one() or 0
    rarity = round(owners * 100.0 / total_users, 2)
    b = db.get(Badge, badge_id)
    owned = db.execute(
        select(func.count()).where(UserBadge.user_id == me.id, UserBadge.badge_id == badge_id)
    ).scalar_one() > 0
    return {
        "id": b.id, "slug": b.slug, "title": b.title, "description": b.description,
        "imageUrl": _norm_media_url(b.image_url), "rarityPct": rarity, "owned": owned
    }
