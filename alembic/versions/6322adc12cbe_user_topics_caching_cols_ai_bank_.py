
# Plantilla de archivos de versión (migraciones)

"""user_topics caching cols (ai/bank/variations)

Revision ID: 6322adc12cbe
Revises: 8118c5095b68
Create Date: 2025-10-01 16:41:48.216654

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '6322adc12cbe'
down_revision = '8118c5095b68'
branch_labels = None
depends_on = None

def _col_exists(bind, table: str, column: str) -> bool:
    res = bind.execute(
        sa.text("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = :t AND column_name = :c
            LIMIT 1
        """),
        {"t": table, "c": column},
    ).fetchone()
    return bool(res)


def upgrade():
    bind = op.get_bind()
    
    # Si no existe, créala con default 1 y not null
    if not _col_exists(bind, "user_topics", "bank_version"):
        op.add_column(
            "user_topics",
            sa.Column("bank_version", sa.Integer(), nullable=False, server_default="1")
        )
        # quita el server_default para que lo maneje la app si quieres
        op.alter_column("user_topics", "bank_version", server_default=None)
    else:
        # Asegura default y valores nulos corregidos
        op.execute(sa.text("ALTER TABLE user_topics ALTER COLUMN bank_version SET DEFAULT 1"))
        op.execute(sa.text("UPDATE user_topics SET bank_version = 1 WHERE bank_version IS NULL"))
        op.execute(sa.text("ALTER TABLE user_topics ALTER COLUMN bank_version SET NOT NULL"))

    # ai_seed_done (bool not null default false)
    if not _col_exists(bind, "user_topics", "ai_seed_done"):
        op.add_column(
            "user_topics",
            sa.Column("ai_seed_done", sa.Boolean(), nullable=False, server_default=sa.text("false"))
        )
        # quita el server_default para que futuros inserts usen la app
        op.alter_column("user_topics", "ai_seed_done", server_default=None)

    # cached_explanation (text)
    if not _col_exists(bind, "user_topics", "cached_explanation"):
        op.add_column("user_topics", sa.Column("cached_explanation", sa.Text(), nullable=True))

    # cached_expl_audio_url (varchar 255)
    if not _col_exists(bind, "user_topics", "cached_expl_audio_url"):
        op.add_column("user_topics", sa.Column("cached_expl_audio_url", sa.String(length=255), nullable=True))

    # cached_visual_image_url (varchar 255)
    if not _col_exists(bind, "user_topics", "cached_visual_image_url"):
        op.add_column("user_topics", sa.Column("cached_visual_image_url", sa.String(length=255), nullable=True))

    # bank_variation_seed (int)
    if not _col_exists(bind, "user_topics", "bank_variation_seed"):
        op.add_column("user_topics", sa.Column("bank_variation_seed", sa.Integer(), nullable=True))

    # times_opened (int not null default 0)
    if not _col_exists(bind, "user_topics", "times_opened"):
        op.add_column(
            "user_topics",
            sa.Column("times_opened", sa.Integer(), nullable=False, server_default="0")
        )
        op.alter_column("user_topics", "times_opened", server_default=None)


def downgrade():
    bind = op.get_bind()

    # El downgrade también debe ser idempotente
    if _col_exists(bind, "user_topics", "times_opened"):
        op.drop_column("user_topics", "times_opened")
    if _col_exists(bind, "user_topics", "bank_variation_seed"):
        op.drop_column("user_topics", "bank_variation_seed")
    if _col_exists(bind, "user_topics", "cached_visual_image_url"):
        op.drop_column("user_topics", "cached_visual_image_url")
    if _col_exists(bind, "user_topics", "cached_expl_audio_url"):
        op.drop_column("user_topics", "cached_expl_audio_url")
    if _col_exists(bind, "user_topics", "cached_explanation"):
        op.drop_column("user_topics", "cached_explanation")
    if _col_exists(bind, "user_topics", "ai_seed_done"):
        op.drop_column("user_topics", "ai_seed_done")