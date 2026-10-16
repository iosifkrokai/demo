"""The data version: content, not a clock, and read back by the planner."""

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
    assert content_version(tmp_path) == before

    (tmp_path / VERSIONED_FILES[0]).write_bytes(b"different")
    assert content_version(tmp_path) != before


def test_an_absent_optional_file_is_part_of_the_version(tmp_path) -> None:
    """Creating a file the projection reads moves the version, not just editing one."""
    from db.seed.version import VERSIONED_FILES, content_version

    _write_tree(tmp_path, VERSIONED_FILES[1:])
    absent = content_version(tmp_path)

    (tmp_path / VERSIONED_FILES[0]).write_bytes(b"now present")
    assert content_version(tmp_path) != absent


def test_the_embedding_recipe_is_part_of_the_version(tmp_path, monkeypatch) -> None:
    """A vector is a projection of the recipe: change the text and every vector is stale."""
    from db.seed import embeddings, version

    assert version.RECIPE == embeddings.RECIPE

    _write_tree(tmp_path, version.VERSIONED_FILES)
    before = version.content_version(tmp_path)

    monkeypatch.setattr(version, "RECIPE", "embed:something-else")
    assert version.content_version(tmp_path) != before


def test_the_seed_stamps_a_version_the_planner_reads_back() -> None:
    """The write and the read agree, and an unchanged version is a true no-op."""
    from db.connection import connect
    from db.seed import pipeline
    from db.store.datasets import DATA_VERSION_ROW, PostgresDatasetRepository

    try:
        conn = connect(autocommit=True)
    except Exception as exc:
        pytest.skip(f"database not reachable: {type(exc).__name__}")

    repo = PostgresDatasetRepository()
    touchable = False
    restored: tuple[str, object] | None = None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('data_versions')")
            if cur.fetchone()[0] is None:
                pytest.skip("data_versions is not migrated in this database")
            touchable = True

            cur.execute(
                "SELECT version, updated_at FROM data_versions WHERE name = %s",
                (DATA_VERSION_ROW,),
            )
            restored = cur.fetchone()

            pipeline.record_data_version(conn, "test-digest-1")
            cur.execute(
                "SELECT version, updated_at FROM data_versions WHERE name = %s",
                (DATA_VERSION_ROW,),
            )
            version, stamped_at = cur.fetchone()
            assert version == "test-digest-1"

            pipeline.record_data_version(conn, "test-digest-1")
            cur.execute("SELECT updated_at FROM data_versions WHERE name = %s", (DATA_VERSION_ROW,))
            assert cur.fetchone()[0] == stamped_at

            pipeline.record_data_version(conn, "test-digest-2")
            cur.execute("SELECT version FROM data_versions WHERE name = %s", (DATA_VERSION_ROW,))
            assert cur.fetchone()[0] == "test-digest-2"

        fresh = PostgresDatasetRepository()
        try:
            assert fresh.version() == "test-digest-2"
        finally:
            fresh.close()
    finally:
        if touchable:
            with conn.cursor() as cur:
                if restored is None:
                    cur.execute("DELETE FROM data_versions WHERE name = %s", (DATA_VERSION_ROW,))
                else:
                    cur.execute(
                        """
                        INSERT INTO data_versions (name, version, updated_at) VALUES (%s, %s, %s)
                        ON CONFLICT (name) DO UPDATE SET
                            version = EXCLUDED.version,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (DATA_VERSION_ROW, restored[0], restored[1]),
                    )
        repo.close()
        conn.close()
