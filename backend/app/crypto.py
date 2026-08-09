"""Encryption for client Delta API secrets at rest.

v1: Fernet (AES-128-CBC + HMAC) with a master key from SECRET_ENCRYPTION_KEY.
In production that master key MUST come from KMS/Vault, not a flat env var, and
ideally per-record envelope encryption (a KMS-wrapped DEK per secret). The call
sites here are KMS-ready: swap `encrypt_secret`/`decrypt_secret` internals only.

Plaintext secrets are NEVER logged and NEVER returned to the frontend.
"""
from cryptography.fernet import Fernet

from .config import settings


def _fernet() -> Fernet:
    key = settings.secret_encryption_key
    if not key:
        raise RuntimeError(
            "SECRET_ENCRYPTION_KEY is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def generate_key() -> str:
    """Helper for ops: print a fresh master key."""
    return Fernet.generate_key().decode()
