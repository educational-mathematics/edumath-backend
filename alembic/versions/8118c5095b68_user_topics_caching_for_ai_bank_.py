
# Plantilla de archivos de versión (migraciones)

"""user_topics caching for ai/bank/variations

Revision ID: 8118c5095b68
Revises: 9d0f948449e8
Create Date: 2025-10-01 15:50:19.376642

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '8118c5095b68'
down_revision = '9d0f948449e8'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column(
        "user_topics",
        sa.Column(
            "ai_seed_done",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "user_topics",
        sa.Column("ai_explanation_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "user_topics",
        sa.Column("ai_explanation_audio_url", sa.String(255), nullable=True),
    )
    op.add_column(
        "user_topics",
        sa.Column("ai_image_url", sa.String(255), nullable=True),
    )
    op.add_column(
        "user_topics",
        sa.Column("bank_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "user_topics",
        sa.Column("last_generation_model", sa.String(80), nullable=True),
    )
    op.add_column(
        "user_topics",
        sa.Column("last_seed_used", sa.Integer(), nullable=True),
    )
    op.add_column(
        "user_topics",
        sa.Column(
            "bank_exercises",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "user_topics",
        sa.Column("bank_variant_counter", sa.Integer(), nullable=False, server_default="0"),
    )

    # Opcional: limpiar server_default para que la app controle los defaults a partir de ahora
    op.alter_column("user_topics", "ai_seed_done", server_default=None)
    op.alter_column("user_topics", "bank_version", server_default=None)
    op.alter_column("user_topics", "bank_variant_counter", server_default=None)


def downgrade():
    op.drop_column("user_topics", "bank_variant_counter")
    op.drop_column("user_topics", "bank_exercises")
    op.drop_column("user_topics", "last_seed_used")
    op.drop_column("user_topics", "last_generation_model")
    op.drop_column("user_topics", "bank_version")
    op.drop_column("user_topics", "ai_image_url")
    op.drop_column("user_topics", "ai_explanation_audio_url")
    op.drop_column("user_topics", "ai_explanation_text")
    op.drop_column("user_topics", "ai_seed_done")