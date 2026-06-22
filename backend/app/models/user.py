"""User ORM model (control plane).

Represents an authenticated platform principal. Passwords are stored only as Argon2id
hashes. ``token_version`` participates in every issued JWT; incrementing it invalidates all
outstanding tokens for the user (logout-everywhere / forced re-auth on password change).

Column types are chosen to be dialect-agnostic (``Uuid``, generic ``Enum``) so the model
works against PostgreSQL in production and SQLite in tests without divergence.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.roles import Role
from app.db.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)

    # Stored normalized to lowercase by the service layer; unique + indexed for login.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[Role] = mapped_column(
        Enum(
            Role,
            name="user_role",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=Role.VIEWER,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Bumped to revoke all previously-issued tokens for this user.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User id={self.id} email={self.email!r} role={self.role.value}>"
