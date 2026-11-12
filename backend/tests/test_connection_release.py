"""Control-plane connection release: read-only transactions end early; writes are never
committed early on the request's behalf."""

from __future__ import annotations

import asyncio
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import release_connection
from app.models.user import User
from app.security import password as pwd


async def test_read_only_transaction_is_released(
    sessionmaker: async_sessionmaker[AsyncSession], seed_users
) -> None:
    async with sessionmaker() as session:
        await session.execute(select(User))
        assert session.in_transaction()
        await release_connection(session)
        assert not session.in_transaction()  # connection returned to the pool
        # Objects stay usable and later queries simply begin a new transaction.
        users = (await session.execute(select(User))).scalars().all()
        assert {u.email for u in users} >= {"admin@test.com"}


async def test_pending_write_is_not_committed_early(
    sessionmaker: async_sessionmaker[AsyncSession], seed_users
) -> None:
    async with sessionmaker() as session:
        user = (await session.execute(select(User).where(User.email == "viewer@test.com"))).scalar_one()
        user.full_name = "Changed"  # dirty, unflushed
        await release_connection(session)
        assert session.in_transaction()
        await session.rollback()
    async with sessionmaker() as session:
        again = (await session.execute(select(User).where(User.email == "viewer@test.com"))).scalar_one()
        assert again.full_name != "Changed"  # the rollback still applied: nothing was committed


async def test_flushed_write_is_not_committed_early(
    sessionmaker: async_sessionmaker[AsyncSession], seed_users
) -> None:
    async with sessionmaker() as session:
        user = (await session.execute(select(User).where(User.email == "viewer@test.com"))).scalar_one()
        user.full_name = "Flushed"
        await session.flush()  # no longer dirty, but written in this transaction
        await release_connection(session)
        assert session.in_transaction()
        await session.rollback()
    async with sessionmaker() as session:
        again = (await session.execute(select(User).where(User.email == "viewer@test.com"))).scalar_one()
        assert again.full_name != "Flushed"


async def test_password_hashing_does_not_block_the_event_loop() -> None:
    # While several Argon2 hashes run, the event loop must keep serving other work promptly.
    hashed = pwd.hash_password("correct-horse-battery")
    gaps: list[float] = []

    async def ticker() -> None:
        last = time.perf_counter()
        for _ in range(20):
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    await asyncio.gather(
        ticker(),
        *(pwd.verify_password_async(hashed, "correct-horse-battery") for _ in range(4)),
    )
    assert await pwd.verify_password_async(hashed, "correct-horse-battery")
    assert not await pwd.verify_password_async(hashed, "wrong")
    # A blocking implementation would show gaps of hundreds of ms (one hash ≈ 0.1-0.3 s).
    assert max(gaps) < 0.1, f"event loop stalled for {max(gaps) * 1000:.0f} ms"
