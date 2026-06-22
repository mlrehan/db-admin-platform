"""Key management for envelope encryption.

The platform uses *envelope encryption*: each secret is encrypted with a freshly-generated
256-bit data-encryption key (DEK); that DEK is then wrapped (encrypted) by a long-lived
key-encryption key (KEK). Only wrapped DEKs are ever persisted.

:class:`KeyManager` is the abstraction over "where the KEK lives and how wrapping happens".
The default :class:`EnvelopeKeyManager` holds the KEK in process memory (sourced from the
``SECURITY_MASTER_ENCRYPTION_KEY`` env var). A future AWS KMS / HashiCorp Vault implementation
can subclass :class:`KeyManager` without any change to callers — this is the seam that keeps
the architecture stable as key custody hardens.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.exceptions import EncryptionError

# Domain-separation tag so a wrapped DEK can never be mistaken for any other GCM ciphertext.
_DEK_WRAP_AAD = b"db-admin-platform:dek-wrap:v1"
_GCM_NONCE_BYTES = 12
_DEK_BYTES = 32  # AES-256


class KeyManager(ABC):
    """Custody of the KEK and the wrap/unwrap operations over DEKs."""

    @abstractmethod
    def generate_data_key(self) -> tuple[bytes, bytes]:
        """Return ``(plaintext_dek, wrapped_dek)``. The plaintext DEK is used immediately
        to encrypt one secret and then discarded; only the wrapped form is persisted."""

    @abstractmethod
    def unwrap_data_key(self, wrapped_dek: bytes) -> bytes:
        """Return the plaintext DEK for a previously-wrapped DEK."""


class EnvelopeKeyManager(KeyManager):
    """KEK held in memory; DEKs wrapped with AES-256-GCM under the KEK."""

    def __init__(self, kek: bytes) -> None:
        if len(kek) != _DEK_BYTES:
            raise EncryptionError("Master key (KEK) must be exactly 32 bytes (AES-256).")
        self._kek = AESGCM(kek)

    def generate_data_key(self) -> tuple[bytes, bytes]:
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(_GCM_NONCE_BYTES)
        wrapped = nonce + self._kek.encrypt(nonce, dek, _DEK_WRAP_AAD)
        return dek, wrapped

    def unwrap_data_key(self, wrapped_dek: bytes) -> bytes:
        if len(wrapped_dek) <= _GCM_NONCE_BYTES:
            raise EncryptionError("Malformed wrapped data key.")
        nonce, ciphertext = wrapped_dek[:_GCM_NONCE_BYTES], wrapped_dek[_GCM_NONCE_BYTES:]
        try:
            return self._kek.decrypt(nonce, ciphertext, _DEK_WRAP_AAD)
        except InvalidTag as exc:
            raise EncryptionError(
                "Failed to unwrap data key — wrong master key or corrupted ciphertext."
            ) from exc
