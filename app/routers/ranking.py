from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, asc
from app.deps import get_db, get_current_user
from app.models.user import User
from app.schemas.ranking import RankingRow
from app.domain.badges.service import award_king_if_top1

router = APIRouter(prefix="/ranking", tags=["ranking"])

# Helpers para ordenar/ranquear: por puntos DESC, luego id ASC (desempate estable).
ORDERING = (desc(User.points), asc(User.id))

@router.get("", response_model=list[RankingRow])
def top_ranking(db: Session = Depends(get_db)):
    """
    Devuelve Top 100 usuarios con alias (visibles en ranking), incluyendo su 'rank'.
    """
    rank_col = func.rank().over(order_by=ORDERING).label("rank")
    q = (
        db.query(
            rank_col,
            User.alias,
            User.points,
            User.avatar_url,
        )
        .filter(User.alias.isnot(None))
        .order_by(*ORDERING)
        .limit(100)
    )
    rows = q.all()
    return [
        RankingRow(
            rank=int(r[0]),
            alias=r[1],
            points=int(r[2] or 0),
            avatar_url=r[3],
        )
        for r in rows
    ]


@router.get("/me", response_model=RankingRow)
def my_rank(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    if not me.alias:
        raise HTTPException(status_code=404, detail="No tienes alias para el ranking")

    # Subquery con rank para todos los que tienen alias
    rank_col = func.rank().over(order_by=ORDERING).label("rank")
    subq = (
        db.query(
            User.id.label("id"),
            User.alias.label("alias"),
            User.points.label("points"),
            User.avatar_url.label("avatar_url"),
            rank_col,
        )
        .filter(User.alias.isnot(None))
        .subquery()
    )

    row = (
        db.query(
            subq.c.rank,
            subq.c.alias,
            subq.c.points,
            subq.c.avatar_url,
        )
        .filter(subq.c.id == me.id)
        .first()
    )

    if not row:
        raise HTTPException(status_code=404, detail="No se encontró tu posición en el ranking")

    return RankingRow(
        rank=int(row[0]),
        alias=row[1],
        points=int(row[2] or 0),
        avatar_url=row[3],
    )
