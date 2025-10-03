
# Plantilla de archivos de versión (migraciones)

"""images

Revision ID: 7018dc9a92eb
Revises: af74bccfbcb6
Create Date: 2025-10-02 21:57:20.733199

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '7018dc9a92eb'
down_revision = 'af74bccfbcb6'
branch_labels = None
depends_on = None

def _has_table(conn, table_name: str) -> bool:
    insp = sa.inspect(conn)
    return table_name in insp.get_table_names()


def _has_column(conn, table_name: str, column_name: str) -> bool:
    insp = sa.inspect(conn)
    try:
        cols = [c["name"] for c in insp.get_columns(table_name)]
    except Exception:
        return False
    return column_name in cols


def _safe_update(conn, sql: str):
    # Ejecuta un UPDATE “silencioso”; si falla, no aborta la migración completa
    try:
        conn.execute(sa.text(sql))
    except Exception as e:
        # Log mínimo; Alembic mostrará el print en consola
        print(f"[WARN] UPDATE ignorado por error: {e}")


def upgrade():
    conn = op.get_bind()

    # ----- BADGES.image_url -----
    if _has_table(conn, "badges") and _has_column(conn, "badges", "image_url"):
        with op.get_context().autocommit_block():
            _safe_update(conn, """
                UPDATE badges
                SET image_url = REPLACE(image_url, '/static/badges/', '/media/badges/')
                WHERE image_url LIKE '/static/badges/%'
            """)
            _safe_update(conn, """
                UPDATE badges
                SET image_url = '/media/' || image_url
                WHERE image_url LIKE 'badges/%'
            """)

    # ----- TOPICS.cover_url -----
    if _has_table(conn, "topics") and _has_column(conn, "topics", "cover_url"):
        with op.get_context().autocommit_block():
            _safe_update(conn, """
                UPDATE topics
                SET cover_url = REPLACE(cover_url, '/static/covers/', '/media/covers/')
                WHERE cover_url LIKE '/static/covers/%'
            """)
            _safe_update(conn, """
                UPDATE topics
                SET cover_url = '/media/' || cover_url
                WHERE cover_url LIKE 'covers/%'
            """)

    # ----- USER/USERS.avatar_url (tabla puede ser user o users) -----
    user_table = None
    for cand in ("user", "users", "auth_user"):
        if _has_table(conn, cand):
            user_table = cand
            break

    if user_table and _has_column(conn, user_table, "avatar_url"):
        with op.get_context().autocommit_block():
            _safe_update(conn, f"""
                UPDATE {sa.sql.text(user_table).text}
                SET avatar_url = REPLACE(avatar_url, '/static/avatars/', '/media/avatars/')
                WHERE avatar_url LIKE '/static/avatars/%'
            """)
            _safe_update(conn, f"""
                UPDATE {sa.sql.text(user_table).text}
                SET avatar_url = '/media/' || avatar_url
                WHERE avatar_url LIKE 'avatars/%'
            """)


def downgrade():
    conn = op.get_bind()

    # ----- BADGES.image_url -----
    if _has_table(conn, "badges") and _has_column(conn, "badges", "image_url"):
        with op.get_context().autocommit_block():
            _safe_update(conn, """
                UPDATE badges
                SET image_url = REPLACE(image_url, '/media/badges/', '/static/badges/')
                WHERE image_url LIKE '/media/badges/%'
            """)
            # si quedó con prefijo /media/... lo quitamos
            _safe_update(conn, """
                UPDATE badges
                SET image_url = SUBSTR(image_url, 8)
                WHERE image_url LIKE '/media/badges/%'
            """)

    # ----- TOPICS.cover_url -----
    if _has_table(conn, "topics") and _has_column(conn, "topics", "cover_url"):
        with op.get_context().autocommit_block():
            _safe_update(conn, """
                UPDATE topics
                SET cover_url = REPLACE(cover_url, '/media/covers/', '/static/covers/')
                WHERE cover_url LIKE '/media/covers/%'
            """)
            _safe_update(conn, """
                UPDATE topics
                SET cover_url = SUBSTR(cover_url, 8)
                WHERE cover_url LIKE '/media/covers/%'
            """)

    # ----- USER/USERS.avatar_url -----
    user_table = None
    for cand in ("user", "users", "auth_user"):
        if _has_table(conn, cand):
            user_table = cand
            break

    if user_table and _has_column(conn, user_table, "avatar_url"):
        with op.get_context().autocommit_block():
            _safe_update(conn, f"""
                UPDATE {sa.sql.text(user_table).text}
                SET avatar_url = REPLACE(avatar_url, '/media/avatars/', '/static/avatars/')
                WHERE avatar_url LIKE '/media/avatars/%'
            """)
            _safe_update(conn, f"""
                UPDATE {sa.sql.text(user_table).text}
                SET avatar_url = SUBSTR(avatar_url, 8)
                WHERE avatar_url LIKE '/media/avatars/%'
            """)