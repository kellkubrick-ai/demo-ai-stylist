from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_stylist.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url, pool_pre_ping=True)
