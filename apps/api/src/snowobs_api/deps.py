"""FastAPI dependency providers.

Long-lived resources (database engine, Redis client) are created once in the
application lifespan and exposed on ``app.state``; handlers depend on these
providers rather than constructing clients per request.
"""

from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from snowobs_common.config import Settings


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_db_engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.db_engine
    return engine


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
DbEngineDep = Annotated[AsyncEngine, Depends(get_db_engine)]
RedisDep = Annotated[Redis, Depends(get_redis)]
