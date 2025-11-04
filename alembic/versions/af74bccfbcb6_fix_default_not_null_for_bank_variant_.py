
# Plantilla de archivos de versión (migraciones)

"""fix default/not-null for bank_variant_counter

Revision ID: af74bccfbcb6
Revises: 6322adc12cbe
Create Date: 2025-10-01 17:12:30.558117

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'af74bccfbcb6'
down_revision = '6322adc12cbe'
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    # si la columna no existe, créala con default 0
    res = bind.execute(sa.text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name='user_topics' AND column_name='bank_variant_counter'
        LIMIT 1
    """)).fetchone()

    if not res:
        op.add_column(
            "user_topics",
            sa.Column("bank_variant_counter", sa.Integer(), nullable=False, server_default="0")
        )
        # (opcional) retirar server_default para que lo maneje la app luego
        op.alter_column("user_topics", "bank_variant_counter", server_default=None)
    else:
        # asegurar default y no-nulo, y rellenar nulos existentes
        op.execute(sa.text("ALTER TABLE user_topics ALTER COLUMN bank_variant_counter SET DEFAULT 0"))
        op.execute(sa.text("UPDATE user_topics SET bank_variant_counter = 0 WHERE bank_variant_counter IS NULL"))
        op.execute(sa.text("ALTER TABLE user_topics ALTER COLUMN bank_variant_counter SET NOT NULL"))

def downgrade():
    # opcional
    op.drop_column("user_topics", "bank_variant_counter")