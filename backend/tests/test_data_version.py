"""The data version: content, not a clock, and read back by the planner.

The version exists so a re-seed invalidates measurements. It is the digest of the
committed datasets (`db.seed.version`), stamped into `data_versions` by the seed
(`db.seed.pipeline.record_data_version`) and read by the planner through
`db.store.datasets`. These tests exercise the digest offline and the round trip
against whatever database `DATABASE_URL` names — the latter skips when the
`data_versions` table is not migrated in, because a skip is honest and a fake pass
is not.
"""

from __future__ import annotations

import pytest


def _write_tree(root, relative_paths) -> None:
    for rel in relative_paths:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"content of {rel}".encode())


def test_the_version_is_content_not_a_clock(tmp_path) -> None:
    """Re-running over unchanged files reproduces it; a changed file moves it."""
    from db.seed.version import VERSIONED_FILES, content_version

    _write_tree(tmp_path, VERSIONED_FILES)

    before = content_version(tmp_path)
    assert content_version(tmp_path) == before  # unchanged files, same version

    (tmp_path / VERSIONED_FILES[0]).write_bytes(b"different")
    assert content_version(tmp_path) != before  # a content change moves the version


def test_an_absent_optional_file_is_part_of_the_version(tmp_path) -> None:
    """Creating a file the projection reads moves the version, not just editing one."""
    from db.seed.version import VERSIONED_FILES, content_version

    _write_tree(tmp_path, VERSIONED_FILES[1:])
    absent = content_version(tmp_path)

    (tmp_path / VERSIONED_FILES[0]).write_bytes(b"now present")
    assert content_version(tmp_path) != absent


def test_the_seed_stamps_a_version_the_planner_reads_back() -> None:
    """The write and the read agree, and an unchanged version is a true no-op.

    Runs against the database `DATABASE_URL` names; skips when `data_versions` is
    not there (it is a new revision, and the shared database must not be migrated
    by a test).
    """
    from db.connection import connect
    from db.seed import pipeline
    from db.store.datasets import DATA_VERSION_ROW, PostgresDatasetRepository

    try:
        conn = connect(autocommit=True)
    except Exception as exc:  # any connection failure means "not reachable here"
        pytest.skip(f"database not reachable: {type(exc).__name__}")

    repo = PostgresDatasetRepository()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('data_versions')")
            if cur.fetchone()[0] is None:
                pytest.skip("data_versions is not migrated in this database")

            pipeline.record_data_version(conn, "test-digest-1")
            cur.execute(
                "SELECT version, updated_at FROM data_versions WHERE name = %s",
                (DATA_VERSION_ROW,),
            )
            version, stamped_at = cur.fetchone()
            assert version == "test-digest-1"

            # Same content, second seed: the version (and its stamp) do not move.
            pipeline.record_data_version(conn, "test-digest-1")
            cur.execute("SELECT updated_at FROM data_versions WHERE name = %s", (DATA_VERSION_ROW,))
            assert cur.fetchone()[0] == stamped_at

            # Changed content: the version moves.
            pipeline.record_data_version(conn, "test-digest-2")
            cur.execute("SELECT version FROM data_versions WHERE name = %s", (DATA_VERSION_ROW,))
            assert cur.fetchone()[0] == "test-digest-2"

        # A fresh reader (no cached read) sees what the seed wrote.
        fresh = PostgresDatasetRepository()
        try:
            assert fresh.version() == "test-digest-2"
        finally:
            fresh.close()
    finally:
        repo.close()
        conn.close()
