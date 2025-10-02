# scripts/seed_badges.py
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from app.db import SessionLocal
from app.models.badge import Badge

SEEDS = [
    # slug,               title,                      description,                                                  image_url
    ("welcome",           "Bienvenido/a",             "Ingresaste por primera vez a EduMath",                      "/static/badges/welcome.png"),
    ("rey",               "El Rey",                   "Alcanza el TOP 1 del ranking y ten al menos 1000 puntos",   "/static/badges/king.png"),
    ("el-mejor",          "El Mejor",                 "Termina todos los temas sin fallar ni una sola vez",        "/static/badges/the_best.png"),
    ("developer",         "Developer",                "Se parte del equipo de desarrollo de EduMath",              "/static/badges/developer.png"),
    ("beta-tester",       "Beta Tester",              "Se parte de los usuarios de la prueba piloto de EduMath",   "/static/badges/beta_tester.png"),
    ("independiente",     "Independiente",            "Termina todas las lecciones sin usar el asistente",         "/static/badges/independent.png"),
    ("pequenos-pasos",    "Pequeños pasos",           "Completa 5 temas",                                          "/static/badges/small_steps.png"),
    ("un-gran-paso",      "Un gran paso",             "Termina todos los temas",                                   "/static/badges/a_big_step.png"),
    ("sed-de-sabiduria",  "Sed de Sabiduría",         "Termina todos los temas 2 veces",                           "/static/badges/thirst_for_wisdom.png"),
    ("alas-cortadas",     "Alas Recortadas",          "Falla solo en la última pregunta de tu último tema restante", "/static/badges/clipped_wings.png"),
    ("principiante-elite","Principiante de Élite",    "Consigue 1000 puntos",                                      "/static/badges/elite_beginner.png"),
    ("estrella-platinada","Estrella Platinada",       "Consigue 10000 puntos",                                     "/static/badges/platinum_star.png"),
    ("leyenda-viva",      "Leyenda Viva",             "Consigue 1000000 de puntos",                                   "/static/badges/living_legend.png"),
]

def upsert_badge(db, slug, title, description, image_url):
    row = db.execute(select(Badge).where(Badge.slug == slug)).scalar_one_or_none()
    if row:
        row.title = title
        row.description = description
        row.image_url = image_url
    else:
        row = Badge(slug=slug, title=title, description=description, image_url=image_url)
        db.add(row)
    db.commit()

def main():
    db = SessionLocal()
    try:
        for slug, title, desc, url in SEEDS:
            upsert_badge(db, slug, title, desc, url)
        print("Badges seed OK")
    finally:
        db.close()

if __name__ == "__main__":
    main()
