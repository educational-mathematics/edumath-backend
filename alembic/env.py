import os
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool
from app.db import Base
from app.models import user, badge, user_badge

# >>> AÑADE ESTAS 2 LÍNEAS <<<
from dotenv import load_dotenv
load_dotenv()
# <<< AÑADE ESTAS 2 LÍNEAS >>>

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Toma DATABASE_URL del entorno (ya cargado del .env)
DATABASE_URL = os.getenv("DATABASE_URL")

# Fallback por si no vino del entorno: intenta leer del engine de tu app
if not DATABASE_URL:
    try:
        from app.db import engine
        DATABASE_URL = str(engine.url)
    except Exception:
        pass

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL no está definido. Define en .env o pon la URL literal en alembic.ini (sqlalchemy.url)."
    )

config.set_main_option("sqlalchemy.url", DATABASE_URL)

from app.db.base import Base  # importa tu metadata con todos los modelos
target_metadata = Base.metadata

def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
