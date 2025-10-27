
# Plantilla de archivos de versión (migraciones)

"""create_assistant_explanations

Revision ID: 37964a4ad2f9
Revises: 7018dc9a92eb
Create Date: 2025-10-27 15:44:11.709995

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '37964a4ad2f9'
down_revision = '7018dc9a92eb'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        'assistant_explanations',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('topic_id', sa.Integer(), nullable=False),
        sa.Column('grade', sa.Integer(), nullable=False),
        sa.Column('style', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('payload', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),

        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_assistant_expl_user_id'),
        sa.ForeignKeyConstraint(['topic_id'], ['topics.id'], name='fk_assistant_expl_topic_id')
    )

    # Índices
    op.create_index('ix_assistant_expl_user_id', 'assistant_explanations', ['user_id'])
    op.create_index('ix_assistant_expl_topic_id', 'assistant_explanations', ['topic_id'])
    op.create_index('ix_assistant_expl_style', 'assistant_explanations', ['style'])
    op.create_index('ix_assistant_expl_status', 'assistant_explanations', ['status'])


def downgrade() -> None:
    op.drop_index('ix_assistant_expl_user_id', table_name='assistant_explanations')
    op.drop_index('ix_assistant_expl_topic_id', table_name='assistant_explanations')
    op.drop_index('ix_assistant_expl_style', table_name='assistant_explanations')
    op.drop_index('ix_assistant_expl_status', table_name='assistant_explanations')
    op.drop_table('assistant_explanations')