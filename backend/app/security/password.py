"""Password hashing using Argon2id.

Argon2id is the OWASP-recommended algorithm for password storage. We never store or compare
plaintext. Hashes are self-describing (algorithm + parameters are embedded), so cost
parameters can be raised over time and :func:`needs_rehash` will flag old hashes for
transparent upgrade on the next successful login.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# Defaults follow argon2-cffi's recommended profile; tuned to be safe for a web login path.
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,  # 64 MiB
    parallelism=4,
)


def hash_password(plaintext: str) -> str:
    """Return an Argon2id hash of ``plaintext``."""
    return _hasher.hash(plaintext)


def verify_password(hashed: str, plaintext: str) -> bool:
    """Return ``True`` iff ``plaintext`` matches ``hashed``.

    Constant-time within argon2's verifier. Any malformed/mismatched hash returns ``False``
    rather than raising, so callers can treat verification as a simple boolean.
    """
    try:
        return _hasher.verify(hashed, plaintext)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(hashed: str) -> bool:
    """Return ``True`` if ``hashed`` was produced with weaker-than-current parameters."""
    try:
        return _hasher.check_needs_rehash(hashed)
    except (InvalidHashError, ValueError):
        # Unparseable hash — force a rehash on next successful authentication.
        return True
