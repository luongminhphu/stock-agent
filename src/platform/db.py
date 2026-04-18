from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from src.platform.config import settings


def _build_engine():
    url = settings.database_url
    is_sqlite = url.startswith("sqlite")

    if is_sqlite:
        # SQLite does not support pool_size / max_overflow;
        # use StaticPool so the same in-memory DB is shared across threads.
        return create_async_engine(
            url,
            echo=settings.is_development,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

    return create_async_engine(
        url,
        echo=settings.is_development,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )


engine = _build_engine()

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models. Import this in each segment's models.py."""
    pass


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency / context manager for DB sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Alias used by routes/readmodel.py and any other callers expecting `get_session`.
get_session = get_db_session
