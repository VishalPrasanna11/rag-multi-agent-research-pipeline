"""Alembic env: URL from app settings (DATABASE_URL), metadata from app.db."""

from alembic import context
from sqlalchemy import create_engine

from app.config import get_settings
from app.db import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=get_settings().database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    conn = context.config.attributes.get("connection")
    if conn is not None:  # called from app.db.init_db with an open connection
        context.configure(
            connection=conn,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=conn.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(get_settings().database_url)
    with engine.connect() as c:
        context.configure(
            connection=c,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=c.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
