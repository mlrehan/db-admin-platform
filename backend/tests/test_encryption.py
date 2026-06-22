"""Phase 3 — credential envelope-encryption tests."""

from __future__ import annotations

import os

import pytest

from app.core.exceptions import EncryptionError
from app.security.encryption import CredentialCipher, EncryptedPayload
from app.security.key_manager import EnvelopeKeyManager


def _cipher(kek: bytes | None = None) -> CredentialCipher:
    return CredentialCipher(EnvelopeKeyManager(kek or os.urandom(32)))


def test_roundtrip() -> None:
    cipher = _cipher()
    secret = "s3cr3t-p@ssw0rd-Ünïcode-😀"
    blob = cipher.encrypt(secret)
    assert cipher.decrypt(blob) == secret


def test_ciphertext_is_not_plaintext_and_unique() -> None:
    cipher = _cipher()
    secret = "same-password"
    blob1 = cipher.encrypt(secret)
    blob2 = cipher.encrypt(secret)
    # Fresh DEK + nonce per call → distinct ciphertexts for identical plaintext.
    assert blob1 != blob2
    assert secret not in blob1


def test_wrong_master_key_cannot_decrypt() -> None:
    blob = _cipher(b"\x01" * 32).encrypt("hello")
    with pytest.raises(EncryptionError):
        _cipher(b"\x02" * 32).decrypt(blob)


def test_tampered_ciphertext_is_rejected() -> None:
    cipher = _cipher()
    payload = EncryptedPayload.deserialize(cipher.encrypt("hello"))
    tampered = EncryptedPayload(
        payload.wrapped_dek, payload.nonce, payload.ciphertext[:-1] + bytes([payload.ciphertext[-1] ^ 0x01])
    ).serialize()
    with pytest.raises(EncryptionError):
        cipher.decrypt(tampered)


def test_malformed_blob_rejected() -> None:
    with pytest.raises(EncryptionError):
        _cipher().decrypt("not-a-valid-blob")


def test_bad_kek_length_rejected() -> None:
    with pytest.raises(EncryptionError):
        EnvelopeKeyManager(b"too-short")
