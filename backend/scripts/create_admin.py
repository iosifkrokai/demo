"""Create (or promote) the first administrator (spec 005 §4).

There is no «first registered user becomes admin» backdoor: the only ways to gain
the ``admin`` role are this script and another admin via ``PATCH
/admin/users/{id}``.

    export DATABASE_URL=postgresql://grodno:***@localhost:5432/grodno
    python scripts/create_admin.py --email boss@example.com            # password prompt
    GRODNO_ADMIN_PASSWORD=... python scripts/create_admin.py --email boss@example.com
    python scripts/create_admin.py --email boss@example.com --password ... --name "Босс"

Idempotent: if the email already has an account, its role is raised to ``admin``
(the password is left untouched); otherwise a new admin is created.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.accounts_models import normalize_email, password_problem
from agent.accounts_store import EmailTaken, PostgresAccountRepository
from agent.passwords import hash_password


def main() -> int:
    ap = argparse.ArgumentParser(description="create or promote an administrator")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", default=None,
                    help="omitted: read from GRODNO_ADMIN_PASSWORD or prompt")
    ap.add_argument("--name", default=None, help="display name")
    args = ap.parse_args()

    email = normalize_email(args.email)
    if email is None:
        print(f"not an email: {args.email!r}", file=sys.stderr)
        return 2

    repo = PostgresAccountRepository()
    existing = repo.get_user_by_email(email)
    if existing is not None:
        updated = repo.update_user(existing["id"], role="admin")
        assert updated is not None
        print(f"promoted to admin: {updated['email']} ({updated['id']})")
        repo.close()
        return 0

    password = args.password or os.environ.get("GRODNO_ADMIN_PASSWORD")
    if not password:
        password = getpass.getpass("password: ")
    problem = password_problem(password)
    if problem is not None:
        print(f"password rejected: {problem}", file=sys.stderr)
        return 2

    try:
        row = repo.create_user(
            uuid.uuid4(),
            email=email,
            password_hash=hash_password(password),
            display_name=(args.name or "").strip() or None,
            role="admin",
        )
    except EmailTaken:
        # Raced with another process between the lookup and the insert.
        print(f"email taken: {email}", file=sys.stderr)
        return 1
    print(f"admin created: {row['email']} ({row['id']})")
    repo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
