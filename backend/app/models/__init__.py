"""Control-plane ORM models.

Importing this package registers every model on the shared metadata (used by Alembic
autogenerate and by ``Base.metadata.create_all`` in tests). New models must be imported here.
"""

from app.models.access_grant import AccessGrant
from app.models.audit import AuditLog
from app.models.connection import Connection
from app.models.user import User

__all__ = ["AccessGrant", "AuditLog", "Connection", "User"]
