
# Plantilla de archivos de versión (migraciones)

"""add explanation to topic_sessions

Revision ID: 4cadbe4c562e
Revises: 57de5540e144
Create Date: 2025-09-29 18:19:53.496870

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '4cadbe4c562e'
down_revision = '57de5540e144'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column(
        "topic_sessions",
        sa.Column("explanation", sa.Text(), nullable=True)
    )

def downgrade():
    op.drop_column("topic_sessions", "explanation")