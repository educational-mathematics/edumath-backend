
# Plantilla de archivos de versión (migraciones)

"""user: add alias points badges

Revision ID: 4d353c196a65
Revises: 65d908cbdc61
Create Date: 2025-09-21 17:23:36.583577

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '4d353c196a65'
down_revision = '65d908cbdc61'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('users', sa.Column('points', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('alias', sa.String(length=32), nullable=True))
    op.add_column('users', sa.Column('badges', sa.JSON(), nullable=True))
    op.create_unique_constraint('uq_users_alias', 'users', ['alias'])

def downgrade():
    op.drop_constraint('uq_users_alias', 'users', type_='unique')
    op.drop_column('users', 'badges')
    op.drop_column('users', 'alias')
    op.drop_column('users', 'points')