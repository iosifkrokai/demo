"""Password hashing and session tokens (spec 005).

Two secrets are handled here, and neither may be recoverable from the database:

* **Passwords** — hashed with :func:`hashlib.scrypt` from the standard library
  (no new runtime dependency, no build step). The stored string is
  self-describing: ``scrypt$n$r$p$<salt_b64>$<hash_b64>``. Because the algorithm
  and its parameters travel with the hash, the KDF can be changed later (argon2,
  higher N) and old hashes still verify — the prefix is what tells them apart.

* **Session tokens** — minted with :func:`secrets.token_urlsafe` and handed to the
  browser once, inside an HttpOnly cookie. Only their SHA-256 digest is stored,
  so a leaked ``user_sessions`` table cannot be replayed as a login.

Verification is constant-time (:func:`hmac.compare_digest`) and never raises on a
malformed stored value: an unreadable hash is simply «does not match».
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# scrypt cost. N=2^15 with r=8 needs 32 MiB per hash; maxmem is raised above the
# OpenSSL default so it is not refused. These are the defaults new hashes carry —
# changing them only affects *new* passwords, since each hash records its own.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_SALT_BYTES = 16
SCRYPT_MAXMEM = 64 * 1024 * 1024

_ALGO = "scrypt"

# 30 days. Long enough that a tourist is not logged out mid-trip, short enough
# that a stolen cookie does not live forever.
SESSION_TTL_S = 30 * 24 * 60 * 60


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAXMEM,
    )


def hash_password(password: str) -> str:
    """Return a self-describing scrypt hash suitable for ``users.password_hash``."""
    salt = secrets.token_bytes(SCRYPT_SALT_BYTES)
    digest = _derive(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"{_ALGO}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check of ``password`` against a stored hash.

    Returns ``False`` for any stored value that is missing, malformed, or uses an
    unknown algorithm — a broken row must read as «wrong password», never as an
    exception that leaks the row's shape or crashes the login endpoint.
    """
    if not stored:
        return False
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != _ALGO:
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = _unb64(parts[4])
        expected = _unb64(parts[5])
    except (ValueError, TypeError):
        return False
    try:
        actual = _derive(password, salt, n, r, p)
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(actual, expected)


def new_session_token() -> str:
    """A fresh, unguessable session token (goes to the browser, not the DB)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """The value actually stored in ``user_sessions.token_hash``."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
