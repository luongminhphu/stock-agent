from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

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
        return create_async_engine(
            url,
            echo=settings.db_echo,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

    return create_async_engine(
        url,
        echo=settings.db_echo,
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
    """FastAPI dependency-style session provider."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager for bot/services."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def upsert_rows(
    session_factory,
    model,
    rows: list[dict] | dict,
    *,
    conflict_columns: list[str] | None = None,
    constraint: str | None = None,
    update_columns: list[str] | None = None,
    log_event: str = "platform.db.upsert_failed",
    **log_ctx,
) -> bool:
    """Upsert một hoặc nhiều row (INSERT ... ON CONFLICT DO UPDATE), fire-and-forget.

    Wave E1: hợp nhất 7 hàm ``_persist_*`` (trend_snapshot, trend_prediction, global_risk,
    intelligence_snapshot, daily_agenda, market_quote_cache) vốn copy cùng một khối
    ``pg_insert(...).on_conflict_do_update(...)`` + ``try/except warning``.

    - Chọn dialect theo engine (PostgreSQL hoặc SQLite — cả hai hỗ trợ ON CONFLICT).
    - ``conflict_columns`` hoặc ``constraint`` chỉ định target xung đột (bắt buộc 1 trong 2).
    - ``update_columns`` mặc định = mọi cột trong row trừ cột xung đột; giá trị lấy từ
      ``excluded`` nên hoạt động cho cả batch nhiều row.
    - Row được sort theo cột xung đột để mọi writer đồng thời lock cùng thứ tự
      (tránh deadlock PostgreSQL với batch chéo).
    - Không bao giờ raise: lỗi → ``logger.warning(log_event, **log_ctx)`` và trả False.
      Trả True khi ghi thành công, False khi bỏ qua (session_factory None / rows rỗng).
    """
    if session_factory is None:
        return False
    row_list = [rows] if isinstance(rows, dict) else list(rows)
    if not row_list:
        return False
    if not conflict_columns and not constraint:
        raise ValueError("upsert_rows: cần conflict_columns hoặc constraint")
    try:
        if conflict_columns:
            row_list.sort(key=lambda r: tuple(str(r.get(c, "")) for c in conflict_columns))
        async with session_factory() as session:
            dialect = session.bind.dialect.name if session.bind is not None else engine.dialect.name
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as dialect_insert
            else:
                from sqlalchemy.dialects.postgresql import insert as dialect_insert
            stmt = dialect_insert(model).values(row_list)
            cols = update_columns or [
                c for c in row_list[0] if c not in set(conflict_columns or ())
            ]
            set_ = {c: getattr(stmt.excluded, c) for c in cols}
            if constraint and dialect != "sqlite":
                stmt = stmt.on_conflict_do_update(constraint=constraint, set_=set_)
            else:
                target = conflict_columns or _constraint_columns(model, constraint)
                stmt = stmt.on_conflict_do_update(index_elements=target, set_=set_)
            await session.execute(stmt)
            await session.commit()
        return True
    except Exception as exc:
        from src.platform.logging import get_logger

        get_logger(__name__).warning(log_event, error=str(exc), model=model.__name__, **log_ctx)
        return False


def _constraint_columns(model, constraint_name: str | None) -> list[str]:
    """SQLite không hỗ trợ ON CONFLICT ON CONSTRAINT → tra cột của UniqueConstraint theo tên."""
    for c in model.__table__.constraints:
        if getattr(c, "name", None) == constraint_name:
            return [col.name for col in c.columns]
    raise ValueError(f"upsert_rows: không tìm thấy constraint {constraint_name!r}")
