"""Credential encryption using AES-256-GCM envelope encryption.

:class:`CredentialCipher` turns a plaintext secret (e.g. a target-database password) into a
self-describing, URL-safe string that bundles the wrapped DEK, the GCM nonce and the
ciphertext::

    v1:<b64 wrapped_dek>:<b64 nonce>:<b64 ciphertext+tag>

Decryption reverses the process via the :class:`~app.security.key_manager.KeyManager`. A
constant associated-data tag binds the ciphertext to this purpose (domain separation). The
master key is never stored alongside the ciphertext, so the persisted blob is useless without
the KEK.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings, get_settings
from app.core.exceptions import EncryptionError
from app.security.key_manager import EnvelopeKeyManager, KeyManager

_SCHEME = "v1"
_CRED_AAD = b"db-admin-platform:credential:v1"
_GCM_NONCE_BYTES = 12


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode("ascii"))


@dataclass(frozen=True)
class EncryptedPayload:
    wrapped_dek: bytes
    nonce: bytes
    ciphertext: bytes

    def serialize(self) -> str:
        return ":".join(
            (_SCHEME, _b64e(self.wrapped_dek), _b64e(self.nonce), _b64e(self.ciphertext))
        )

    @classmethod
    def deserialize(cls, blob: str) -> EncryptedPayload:
        try:
            scheme, wrapped, nonce, ct = blob.split(":")
        except ValueError as exc:
            raise EncryptionError("Malformed encrypted credential.") from exc
        if scheme != _SCHEME:
            raise EncryptionError(f"Unsupported credential scheme: {scheme!r}.")
        return cls(_b64d(wrapped), _b64d(nonce), _b64d(ct))


class CredentialCipher:
    def __init__(self, key_manager: KeyManager) -> None:
        self._km = key_manager

    def encrypt(self, plaintext: str) -> str:
        dek, wrapped = self._km.generate_data_key()
        nonce = os.urandom(_GCM_NONCE_BYTES)
        ciphertext = AESGCM(dek).encrypt(nonce, plaintext.encode("utf-8"), _CRED_AAD)
        return EncryptedPayload(wrapped, nonce, ciphertext).serialize()

    def decrypt(self, blob: str) -> str:
        payload = EncryptedPayload.deserialize(blob)
        dek = self._km.unwrap_data_key(payload.wrapped_dek)
        try:
            plaintext = AESGCM(dek).decrypt(payload.nonce, payload.ciphertext, _CRED_AAD)
        except InvalidTag as exc:
            raise EncryptionError("Failed to decrypt credential (integrity check).") from exc
        return plaintext.decode("utf-8")


@lru_cache(maxsize=1)
def _cipher_for(master_key: bytes) -> CredentialCipher:
    return CredentialCipher(EnvelopeKeyManager(master_key))


def get_credential_cipher(settings: Settings | None = None) -> CredentialCipher:
    """Return the process-wide credential cipher built from the configured master key."""
    settings = settings or get_settings()
    master_key = settings.security.master_key_bytes()
    if not master_key:
        raise EncryptionError("SECURITY_MASTER_ENCRYPTION_KEY is not configured.")
    return _cipher_for(master_key)
