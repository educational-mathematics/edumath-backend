import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from app.db import SessionLocal
from app.models.topic import Topic

SEEDS = [
    {"grade":3, "slug":"fracciones-basicas", "title":"Fracciones básicas", "cover_url":"/static/covers/fracciones.png"},
]

def upsert(db, data):
    row = db.execute(select(Topic).where(Topic.slug==data["slug"])).scalar_one_or_none()
    if row:
        row.grade = data["grade"]
        row.title = data["title"]
        row.cover_url = data["cover_url"]
    else:
        row = Topic(**data); db.add(row)
    db.commit()

def main():
    db = SessionLocal()
    try:
        for d in SEEDS: upsert(db, d)
        print("Topics seed OK")
    finally:
        db.close()

if __name__ == "__main__":
    main()