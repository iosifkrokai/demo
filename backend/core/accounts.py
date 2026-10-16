"""What makes an account: the rules, and the codes they answer with."""

from __future__ import annotations

import re

REASON_WEAK_PASSWORD = "weak_password"
REASON_INVALID_EMAIL = "invalid_email"

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 200
DISPLAY_NAME_MAX = 120
EMAIL_MAX = 254

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(raw: str | None) -> str | None:
    """Lower-case and trim an address; ``None`` when it is not one."""
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value or len(value) > EMAIL_MAX or not _EMAIL_RE.match(value):
        return None
    return value


def password_problem(raw: str | None) -> str | None:
    """The reason code a password fails with, or ``None`` when it is acceptable."""
    if raw is None or len(raw) < PASSWORD_MIN_LENGTH:
        return REASON_WEAK_PASSWORD
    if len(raw) > PASSWORD_MAX_LENGTH:
        return REASON_WEAK_PASSWORD
    return None
