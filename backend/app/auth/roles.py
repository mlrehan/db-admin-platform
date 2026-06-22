"""RBAC: roles, permissions, and their mapping.

Four roles are defined by the architecture: Admin, DBA, Developer, Viewer. Authorization is
expressed in terms of fine-grained :class:`Permission` values; each role is granted a fixed
set of permissions. API handlers depend on permissions (not roles directly) so the policy
can evolve without rewriting endpoints.

The permission set is forward-looking — it names capabilities that later phases enforce
(connection management, query execution, destructive-SQL gating, audit access). Defining
them here keeps the authorization model stable as those phases land.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"
    DBA = "dba"
    DEVELOPER = "developer"
    VIEWER = "viewer"


class Permission(str, Enum):
    # Platform administration
    USER_MANAGE = "user:manage"
    AUDIT_READ = "audit:read"

    # Connections (target databases)
    CONNECTION_MANAGE = "connection:manage"  # create/update/delete saved connections
    CONNECTION_USE = "connection:use"  # open a session against a connection

    # Schema / metadata
    SCHEMA_READ = "schema:read"

    # Query execution
    QUERY_READ = "query:read"  # SELECT / read-only statements
    QUERY_WRITE = "query:write"  # INSERT/UPDATE/DELETE
    QUERY_DESTRUCTIVE = "query:destructive"  # DROP/TRUNCATE/ALTER and other DDL

    # Target-database principal management
    DB_USER_MANAGE = "db_user:manage"  # manage users/roles inside target databases


# Explicit role → permission grants.
#
# Access model: the **Admin** is the only authority — they manage users, connections, access
# grants and the audit log, and may run anything (they bypass grant enforcement). Every other
# role (DBA / Developer / Viewer) has **no implicit access**: they may attempt to use a
# connection and browse its schema, but *what they can actually see and do is decided entirely
# by the admin-defined access grants* (default-deny). Non-admins cannot create, edit or delete
# connections. The three non-admin roles are otherwise identical — they exist as convenient
# **grant subjects** (e.g. "grant all Developers SELECT on db X").
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.DBA: frozenset({Permission.CONNECTION_USE, Permission.SCHEMA_READ}),
    Role.DEVELOPER: frozenset({Permission.CONNECTION_USE, Permission.SCHEMA_READ}),
    Role.VIEWER: frozenset({Permission.CONNECTION_USE, Permission.SCHEMA_READ}),
}


def permissions_for(role: Role) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def role_has_permission(role: Role, permission: Permission) -> bool:
    return permission in permissions_for(role)
