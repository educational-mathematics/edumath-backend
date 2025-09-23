from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.user import User
from app.domain.badges.service import on_first_login_done

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
    return {"ok": True, "awardedWelcome": awarded}
