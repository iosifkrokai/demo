"""Create or promote the first administrator.

There is no «first registered user becomes admin» backdoor: the only ways to gain
the ``admin`` role are this command and another admin via
``PATCH /admin/users/{id}``.

    python -m seed admin --email boss@example.com            # password prompt
    GRODNO_ADMIN_PASSWORD=... python -m seed admin --email boss@example.com

Idempotent: if the email already has an account, its role is raised to ``admin``
(the password is left untouched); otherwise a new admin is created.
"""

from __future__ import annotations

import getpass
import os
import sys
import uuid

from contracts.accounts import normalize_email, password_problem
from domain.passwords import hash_password
from store.accounts_store import EmailTaken, PostgresAccountRepository


def create_admin(
    *, email: str, password: str | None = None, name: str | None = None
) -> int:
    """Create the admin (or promote an existing account). Returns an exit code."""
    normalized = normalize_email(email)
    if normalized is None:
        print(f"not an email: {email!r}", file=sys.stderr)
        return 2

    repo = PostgresAccountRepository()
    try:
        existing = repo.get_user_by_email(normalized)
        if existing is not None:
            updated = repo.update_user(existing["id"], role="admin")
            assert updated is not None
            print(f"promoted to admin: {updated['email']} ({updated['id']})")
            return 0

        secret = password or os.environ.get("GRODNO_ADMIN_PASSWORD")
        if not secret:
            secret = getpass.getpass("password: ")
        problem = password_problem(secret)
        if problem is not None:
            print(f"password rejected: {problem}", file=sys.stderr)
            return 2

        try:
            row = repo.create_user(
                uuid.uuid4(),
                email=normalized,
                password_hash=hash_password(secret),
                display_name=(name or "").strip() or None,
                role="admin",
            )
        except EmailTaken:
            # Raced with another process between the lookup and the insert.
            print(f"email taken: {normalized}", file=sys.stderr)
            return 1
        print(f"admin created: {row['email']} ({row['id']})")
        return 0
    finally:
        repo.close()
