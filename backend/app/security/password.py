"""Password hashing using Argon2id.

Argon2id is the OWASP-recommended algorithm for password storage. We never store or compare
plaintext. Hashes are self-describing (algorithm + parameters are embedded), so cost
parameters can be raised over time and :func:`needs_rehash` will flag old hashes for
transparent upgrade on the next successful login.
"""

from __future__ import annotations

import asyncio
import os

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


# --- async wrappers for the request path ---------------------------------------------------
#
# Argon2 is deliberately expensive (~0.1-0.3 s CPU, 64 MiB per hash). Called directly inside an
# async endpoint it would freeze the whole single-worker event loop, so one burst of logins
# stalls every other user's queries. argon2-cffi releases the GIL, so running it in worker
# threads keeps the loop responsive AND hashes in parallel. A semaphore bounds concurrency so a
# login storm can't allocate 64 MiB x N at once.

_HASH_SLOTS = max(2, min(8, (os.cpu_count() or 2)))
_hash_semaphore: asyncio.Semaphore | None = None


def _slots() -> asyncio.Semaphore:
    global _hash_semaphore
    if _hash_semaphore is None:
        _hash_semaphore = asyncio.Semaphore(_HASH_SLOTS)
    return _hash_semaphore


async def hash_password_async(plaintext: str) -> str:
    async with _slots():
        return await asyncio.to_thread(hash_password, plaintext)


async def verify_password_async(hashed: str, plaintext: str) -> bool:
    async with _slots():
        return await asyncio.to_thread(verify_password, hashed, plaintext)
