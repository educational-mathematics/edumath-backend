from sqlalchemy.orm import Session
from sqlalchemy import select, desc, func
from typing import List, Iterable, Set
from app.models.user import User
from app.models.badge import Badge
from app.models.user_badge import UserBadge
from app.models.topic import Topic
from app.models.user_topic import UserTopic
from app.models.topic_session import TopicSession

class BadgeNotFound(Exception): ...
class BadgeAlreadyOwned(Exception): ...

POINTS_THRESHOLDS = [
    (1_000,     "principiante-elite"),
    (10_000,    "estrella-platinada"),
    (1_000_000, "leyenda-viva"),
]

BADGE_RULES = {
    # ---- Por puntos acumulados ----
    "principiante-elite": {
        "title": "Principiante de Élite",
        "description": "Consigue 1000 puntos",
        "condition": lambda user, db: int(user.points or 0) >= 1000
    },
    "estrella-platinada": {
        "title": "Estrella Platinada",
        "description": "Consigue 10000 puntos",
        "condition": lambda user, db: int(user.points or 0) >= 10000
    },
    "leyenda-viva": {
        "title": "Leyenda Viva",
        "description": "Consigue 1000000 de puntos",
        "condition": lambda user, db: int(user.points or 0) >= 1000000
    },

    # ---- Por progreso en temas ----
    "un-gran-paso": {
        "title": "Un gran paso",
        "description": "Termina todos los temas",
        "condition": lambda user, db: all((ut.completed_count or 0) >= 1
                                            for ut in db.query(UserTopic).filter_by(user_id=user.id))
    },
    "sed-de-sabiduria": {
        "title": "Sed de Sabiduría",
        "description": "Termina todos los temas 2 veces",
        "condition": lambda user, db: all((ut.completed_count or 0) >= 2
                                            for ut in db.query(UserTopic).filter_by(user_id=user.id))
    },
    "pequenos-pasos": {
        "title": "Pequeños pasos",
        "description": "Completa 5 temas",
        "condition": lambda user, db: db.query(UserTopic).filter(
            UserTopic.user_id == user.id,
            (UserTopic.completed_count or 0) >= 1
        ).count() >= 5
    },

    # ---- Condiciones especiales ----
    "el-mejor": {
        "title": "El Mejor",
        "description": "Termina todos los temas sin fallar ni una sola vez",
        "condition": lambda user, db: all(
            (ut.completed_count or 0) >= 1 and (ut.errors_total or 0) == 0
            for ut in db.query(UserTopic).filter_by(user_id=user.id)
        )
    },
    "alas-cortadas": {
        "title": "Alas Recortadas",
        "description": "Falla solo en la última pregunta de tu último tema restante",
        "condition": lambda user, db: _check_last_topic_only_last_wrong(user, db)
    },
}

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
        
    for threshold, slug in POINTS_THRESHOLDS:
        if old_points < threshold <= new_points:
            awarded.append(slug)

    return awarded

# --------------------------
# Terminación de temas
# --------------------------

def _count_total_topics(db: Session) -> int:
    return int(db.execute(select(func.count(Topic.id))).scalar_one() or 0)

def _count_completed_once(db: Session, user_id: int) -> int:
    # consideramos terminado si progress_pct >= 100 (al menos 1 vez)
    return int(
        db.execute(
            select(func.count(UserTopic.id))
            .where(UserTopic.user_id == user_id, (UserTopic.progress_pct >= 100))
        ).scalar_one() or 0
    )

def _count_completed_twice_or_more(db: Session, user_id: int) -> int:
    return int(
        db.execute(
            select(func.count(UserTopic.id))
            .where(UserTopic.user_id == user_id, (UserTopic.completed_count >= 2))
        ).scalar_one() or 0
    )

def _user_has_perfect_first_run_all(db: Session, user_id: int, n_topics: int) -> bool:
    """
    'Primera vuelta perfecta': cada tema debe tener completed_count == 1 y errors_total == 0
    (es decir, la primera vez que lo completó no cometió errores).
    """
    if n_topics == 0:
        return False
    cnt = int(
        db.execute(
            select(func.count(UserTopic.id))
            .where(
                UserTopic.user_id == user_id,
                UserTopic.completed_count == 1,
                (UserTopic.errors_total == 0),
            )
        ).scalar_one() or 0
    )
    # Para ser "perfect first run", debe tener EXACTAMENTE una finalización en cada tema y sin errores.
    # Si aún no terminó todos, no aplica.
    return cnt == n_topics

def _failed_only_last_question_this_session(sess: TopicSession) -> bool:
    """
    Devuelve True si en esta sesión SOLO se falló la última pregunta.
    Asume que en `sess.items` cada item puede tener "__wrongAttempts" persistido.
    """
    items = (sess.items or [])
    if not items:
        return False
    total_wrong = 0
    last_idx = len(items) - 1
    last_wrong = 0

    for i, it in enumerate(items):
        w = int((it or {}).get("__wrongAttempts") or 0)
        total_wrong += w
        if i == last_idx:
            last_wrong = w
        elif w != 0:
            # falló en alguna previa → no cumple
            return False

    # “Solo falló la última”: total de errores = errores de la última, y ese total >= 1
    return total_wrong == last_wrong and last_wrong >= 1

def on_topic_finished_awards(db: Session, user_id: int, just_finished_ut: UserTopic, session: TopicSession) -> List[str]:
    """
    Reglas de badges por finalización de temas.
    Se llama DESPUÉS de actualizar el UserTopic y la TopicSession y de hacer commit
    (o al menos con objetos en estado consistente).
    """
    slugs: Set[str] = set()

    n_topics = _count_total_topics(db)
    if n_topics == 0:
        return []

    completed_once = _count_completed_once(db, user_id)
    completed_twice = _count_completed_twice_or_more(db, user_id)

    # 5 temas completados (al menos una vez)
    if completed_once >= 5:
        slugs.add("pequenos-pasos")

    # todos al menos 1 vez
    if completed_once == n_topics:
        slugs.add("un-gran-paso")

    # todos al menos 2 veces
    if completed_twice == n_topics and n_topics > 0:
        slugs.add("sed-de-sabiduria")

    # todos en primera vuelta sin errores
    if completed_once == n_topics and _user_has_perfect_first_run_all(db, user_id, n_topics):
        slugs.add("el-mejor")

    # “falló solo la última pregunta del último tema nuevo”
    # Condiciones:
    # - este UserTopic se terminó por PRIMERA vez (completed_count == 1)
    # - con esto, el usuario alcanzó completed_once == n_topics
    # - en esta sesión, solo hubo errores en la ÚLTIMA pregunta
    if int(just_finished_ut.completed_count or 0) == 1 and completed_once == n_topics:
        if _failed_only_last_question_this_session(session):
            slugs.add("alas-cortadas")

    return list(slugs)

def _check_last_topic_only_last_wrong(user, db):
    # último UserTopic agregado
    ut = db.query(UserTopic).filter_by(user_id=user.id).order_by(UserTopic.id.desc()).first()
    if not ut or (ut.completed_count or 0) < 1:
        return False

    # última sesión de ese tema
    sess = db.query(TopicSession).filter_by(user_id=user.id, topic_id=ut.topic_id).order_by(TopicSession.id.desc()).first()
    if not sess or not sess.results:
        return False

    total = len(sess.results)
    wrong_indices = [i for i, r in enumerate(sess.results) if r.get("correct") is False]

    # condición: solo 1 error y debe ser en la última pregunta
    return len(wrong_indices) == 1 and wrong_indices[0] == total - 1