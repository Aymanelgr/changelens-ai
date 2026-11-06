from logging.config import fileConfig

from alembic import context

from app.core import all_models  # noqa: F401  (autogenerate must see every table)
from app.core.db import Base, engine

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
