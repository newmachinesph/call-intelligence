"""Alembic migration environment for call_intelligence_domain only."""

from logging.config import fileConfig

from alembic import context

from call_intelligence_api import (
    call_models,  # noqa: F401  # registers call metadata
    crm_models,  # noqa: F401  # registers CRM metadata
    models,  # noqa: F401  # registers metadata
)
from call_intelligence_api.db import Base, create_domain_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_online() -> None:
    with create_domain_engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
