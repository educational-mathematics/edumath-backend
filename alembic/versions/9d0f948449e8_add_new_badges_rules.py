
# Plantilla de archivos de versión (migraciones)

"""add new badges rules

Revision ID: 9d0f948449e8
Revises: 4cadbe4c562e
Create Date: 2025-10-01 13:21:02.750157

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import table, column
from sqlalchemy import String, Integer, Text

# revision identifiers, used by Alembic.
revision = '9d0f948449e8'
down_revision = '4cadbe4c562e'
branch_labels = None
depends_on = None

BADGES = [
    {"slug": "principiante-elite", "title": "Principiante de Élite", "description": "Consigue 1000 puntos", "image_url": "/static/badges/elite_beginner.png"},
    {"slug": "estrella-platinada", "title": "Estrella Platinada", "description": "Consigue 10000 puntos", "image_url": "/static/badges/platinum_star.png"},
    {"slug": "leyenda-viva", "title": "Leyenda Viva", "description": "Consigue 1000000 de puntos", "image_url": "/static/badges/living_legend.png"},
    {"slug": "un-gran-paso", "title": "Un gran paso", "description": "Termina todos los temas", "image_url": "/static/badges/a_big_step.png"},
    {"slug": "sed-de-sabiduria", "title": "Sed de Sabiduría", "description": "Termina todos los temas 2 veces", "image_url": "/static/badges/thirst_for_wisdom.png"},
    {"slug": "pequenos-pasos", "title": "Pequeños pasos", "description": "Completa 5 temas", "image_url": "/static/badges/small_steps.png"},
    {"slug": "el-mejor", "title": "El Mejor", "description": "Termina todos los temas sin fallar ni una sola vez", "image_url": "/static/badges/the_best.png"},
    {"slug": "alas-cortadas", "title": "Alas Recortadas", "description": "Falla solo en la última pregunta de tu último tema restante", "image_url": "/static/badges/clipped_wings.png"},
]

def upgrade():
    conn = op.get_bind()
    stmt = sa.text("""
        INSERT INTO badges (slug, title, description, image_url)
        VALUES (:slug, :title, :description, :image_url)
        ON CONFLICT (slug) DO NOTHING;
    """)
    for b in BADGES:
        conn.execute(stmt, b)   # <-- pasar dict como 2º argumento

def downgrade():
    conn = op.get_bind()
    stmt = sa.text("DELETE FROM badges WHERE slug = ANY(:slugs)")
    conn.execute(stmt, {"slugs": [b["slug"] for b in BADGES]})