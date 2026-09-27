"""Encryption for tokens at rest and signed, expiring browser cookies (spec 6.5).

One Fernet key (``DEVAGENT_SECRET_KEY``) is used with a per-purpose label, so a
session cookie can never be replayed as an OAuth state value or vice versa.
"""

import json
import secrets
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr


class CryptoError(Exception):
    """A token or cookie could not be decrypted or verified."""


def generate_key() -> str:
    return Fernet.generate_key().decode()


class SecretBox:
    def __init__(self, key: SecretStr) -> None:
        try:
            self._fernet = Fernet(key.get_secret_value().encode())
        except ValueError as exc:
            raise CryptoError(
                "DEVAGENT_SECRET_KEY must be a Fernet key (32 url-safe base64-encoded bytes); "
                "generate one with: python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'"
            ) from exc

    # ---------------------------------------------------------------- tokens at rest

    def encrypt_token(self, token: SecretStr) -> bytes:
        return self._fernet.encrypt(token.get_secret_value().encode())

    def decrypt_token(self, ciphertext: bytes) -> SecretStr:
        try:
            return SecretStr(self._fernet.decrypt(ciphertext).decode())
        except InvalidToken as exc:
            raise CryptoError("stored token cannot be decrypted with the current key") from exc

    # ---------------------------------------------------------------- signed cookies

    def seal(self, purpose: str, payload: dict[str, Any]) -> str:
        return self._fernet.encrypt(json.dumps({"p": purpose, "d": payload}).encode()).decode()

    def unseal(self, purpose: str, value: str, max_age_seconds: int) -> dict[str, Any]:
        try:
            raw = self._fernet.decrypt(value.encode(), ttl=max_age_seconds)
        except InvalidToken as exc:
            raise CryptoError("cookie is invalid or expired") from exc
        data = json.loads(raw)
        if (
            not isinstance(data, dict)
            or data.get("p") != purpose
            or not isinstance(data.get("d"), dict)
        ):
            raise CryptoError("cookie was issued for another purpose")
        payload: dict[str, Any] = data["d"]
        return payload


def new_state() -> str:
    return secrets.token_urlsafe(24)
