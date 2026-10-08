import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from ai_stylist.catalog.repository import CatalogRepository


@pytest.fixture
async def repository(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL for PostgreSQL/pgvector integration tests")
    database = make_url(url).database
    if not database or not database.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL database name must end in _test")
    schema = "stylist_test_" + uuid4().hex
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "3")
    engine = create_async_engine(url, execution_options={"schema_translate_map": {None: schema}})
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        config = Config("alembic.ini")
        config.attributes["version_table_schema"] = schema

        def migrate(sync_connection):
            config.attributes["connection"] = sync_connection
            command.upgrade(config, "head")

        await connection.run_sync(migrate)
    try:
        yield CatalogRepository(engine)
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()
