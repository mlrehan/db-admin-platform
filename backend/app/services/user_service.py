"""User service — persistence and business logic for platform principals.

Encapsulates all access to the ``users`` table. Handlers and other services depend on this
class rather than issuing queries directly, keeping authentication policy in one place.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.roles import Role
from app.core.exceptions import ConflictError, NotFoundError
from app.models.user import User
from app.security import password as pwd


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- reads ---------------------------------------------------------------------------

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        normalized = email.strip().lower()
        result = await self._session.execute(
            select(User).where(User.email == normalized)
        )
        return result.scalar_one_or_none()

    async def list_users(self, *, limit: int = 100, offset: int = 0) -> list[User]:
        result = await self._session.execute(
            select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def count(self) -> int:
        result = await self._session.execute(select(func.count()).select_from(User))
        return int(result.scalar_one())

    # --- writes --------------------------------------------------------------------------

    async def create_user(
        self,
        *,
        email: str,
        password: str,
        role: Role,
        full_name: str | None = None,
        is_active: bool = True,
    ) -> User:
        normalized = email.strip().lower()
        if await self.get_by_email(normalized) is not None:
            raise ConflictError("A user with this email already exists.")
        user = User(
            email=normalized,
            full_name=full_name,
            hashed_password=pwd.hash_password(password),
            role=role,
            is_active=is_active,
        )
        self._session.add(user)
        await self._session.flush()  # populate defaults (id) within the transaction
        return user

    async def update_profile(
        self,
        user: User,
        *,
        full_name: str | None = None,
        role: Role | None = None,
        is_active: bool | None = None,
    ) -> User:
        if full_name is not None:
            user.full_name = full_name
        if role is not None:
            user.role = role
        if is_active is not None:
            # Deactivating a user must also revoke their live tokens.
            if user.is_active and not is_active:
                user.token_version += 1
            user.is_active = is_active
        await self._session.flush()
        return user

    async def change_password(
        self, user: User, *, new_password: str, revoke_sessions: bool = True
    ) -> User:
        user.hashed_password = pwd.hash_password(new_password)
        if revoke_sessions:
            user.token_version += 1
        await self._session.flush()
        return user

    async def revoke_all_sessions(self, user: User) -> User:
        user.token_version += 1
        await self._session.flush()
        return user

    async def record_login(self, user: User) -> None:
        user.last_login_at = datetime.now(tz=timezone.utc)
        await self._session.flush()

    async def delete_user(self, user_id: uuid.UUID) -> None:
        user = await self.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        await self._session.delete(user)
        await self._session.flush()

    # --- authentication ------------------------------------------------------------------

    async def authenticate(self, *, email: str, password: str) -> User | None:
        """Return the user iff credentials are valid and the account is active.

        Performs a dummy hash on unknown emails to keep response time uniform (mitigates
        user-enumeration via timing). Transparently upgrades legacy hashes.
        """
        user = await self.get_by_email(email)
        if user is None:
            pwd.hash_password(password)  # equalize timing; result discarded
            return None
        if not pwd.verify_password(user.hashed_password, password):
            return None
        if not user.is_active:
            return None
        if pwd.needs_rehash(user.hashed_password):
            user.hashed_password = pwd.hash_password(password)
            await self._session.flush()
        return user
