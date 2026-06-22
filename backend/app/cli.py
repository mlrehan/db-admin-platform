"""Management CLI.

A minimal, dependency-free (stdlib ``argparse``) admin tool. The most important command is
``create-admin``, used to bootstrap the very first Admin principal — without it there is no
way to authenticate into a freshly-migrated database.

Usage::

    python -m app.cli create-admin --email admin@example.com [--password ... | prompt]
    python -m app.cli reset-password --email admin@example.com
    python -m app.cli list-users
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app.auth.roles import Role
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db import session as db_session
from app.services.user_service import UserService


def _prompt_password(confirm: bool = True) -> str:
    pwd = getpass.getpass("Password: ")
    if confirm and pwd != getpass.getpass("Confirm password: "):
        print("Passwords do not match.", file=sys.stderr)
        raise SystemExit(2)
    if len(pwd) < 12:
        print("Password must be at least 12 characters.", file=sys.stderr)
        raise SystemExit(2)
    return pwd


async def _bootstrap_engine() -> None:
    settings = get_settings()
    configure_logging(settings.logging)
    db_session.init_engine(settings.control_db)


async def cmd_create_admin(email: str, password: str | None, full_name: str | None) -> int:
    await _bootstrap_engine()
    pwd = password or _prompt_password()
    async with db_session.session_scope() as session:
        service = UserService(session)
        if await service.get_by_email(email) is not None:
            print(f"User '{email}' already exists.", file=sys.stderr)
            return 1
        user = await service.create_user(
            email=email, password=pwd, role=Role.ADMIN, full_name=full_name
        )
        print(f"Created admin user {user.email} ({user.id})")
    await db_session.dispose_engine()
    return 0


async def cmd_reset_password(email: str, password: str | None) -> int:
    await _bootstrap_engine()
    pwd = password or _prompt_password()
    async with db_session.session_scope() as session:
        service = UserService(session)
        user = await service.get_by_email(email)
        if user is None:
            print(f"No user '{email}'.", file=sys.stderr)
            return 1
        await service.change_password(user, new_password=pwd)
        print(f"Password reset for {user.email}; all sessions revoked.")
    await db_session.dispose_engine()
    return 0


async def cmd_list_users() -> int:
    await _bootstrap_engine()
    async with db_session.session_scope() as session:
        users = await UserService(session).list_users(limit=500)
        for u in users:
            state = "active" if u.is_active else "disabled"
            print(f"{u.id}  {u.email:<40} {u.role.value:<10} {state}")
    await db_session.dispose_engine()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="DB Admin Platform CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_admin = sub.add_parser("create-admin", help="Create the first Admin user")
    p_admin.add_argument("--email", required=True)
    p_admin.add_argument("--password", help="If omitted, prompt securely")
    p_admin.add_argument("--full-name", default=None)

    p_reset = sub.add_parser("reset-password", help="Reset a user's password")
    p_reset.add_argument("--email", required=True)
    p_reset.add_argument("--password", help="If omitted, prompt securely")

    sub.add_parser("list-users", help="List all users")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "create-admin":
        return asyncio.run(cmd_create_admin(args.email, args.password, args.full_name))
    if args.command == "reset-password":
        return asyncio.run(cmd_reset_password(args.email, args.password))
    if args.command == "list-users":
        return asyncio.run(cmd_list_users())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
