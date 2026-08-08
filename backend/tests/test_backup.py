"""Database snapshots, and the ways a backup can lie about having worked.

The failure this guards against is not "the backup crashed" — that is visible.
It is a backup that reports success and produced something unrestorable: a file
copied out from under WAL, a snapshot interrupted halfway and left under a name
retention counts, or a `.part` that a restore script picks as the newest.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.services import backup


def make_database(path: Path, rows: int = 50) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        # WAL is what the application runs in, and the whole reason a file copy
        # is not good enough — committed data can live in the -wal sidecar.
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE skus (id INTEGER PRIMARY KEY, code TEXT)")
        db.executemany("INSERT INTO skus (code) VALUES (?)", [(f"A-{i}",) for i in range(rows)])
        db.commit()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    source = tmp_path / "data" / "stocksync.db"
    make_database(source)
    return Settings(
        database_url=f"sqlite+pysqlite:///{source.as_posix()}",
        backup_dir=tmp_path / "backups",
        env="test",
    )


def count(path: Path) -> int:
    with closing(sqlite3.connect(path)) as db:
        return int(db.execute("SELECT COUNT(*) FROM skus").fetchone()[0])


class TestSnapshot:
    def test_it_writes_a_readable_copy_of_the_data(self, settings: Settings) -> None:
        destination = backup.snapshot(settings)

        assert destination.is_file()
        assert count(destination) == 50

    def test_it_captures_writes_that_are_still_in_the_wal(self, settings: Settings) -> None:
        """The reason this uses SQLite's backup API rather than copying the file.

        These rows are committed but may not be checkpointed into the main
        database yet. `cp stocksync.db` would miss them and still produce a file
        that opens cleanly — a backup that looks fine and is short.
        """
        source = settings.sqlite_path()
        assert source is not None
        with closing(sqlite3.connect(source)) as db:
            db.executemany("INSERT INTO skus (code) VALUES (?)", [(f"B-{i}",) for i in range(25)])
            db.commit()

        assert count(backup.snapshot(settings)) == 75

    def test_it_creates_the_backup_directory(self, settings: Settings) -> None:
        assert not settings.backup_dir.exists()

        backup.snapshot(settings)

        assert settings.backup_dir.is_dir()

    def test_the_name_carries_a_sortable_timestamp(self, settings: Settings) -> None:
        """Retention slices a sorted list; that only works if the name sorts."""
        when = datetime(2026, 8, 7, 11, 45, 0)

        assert backup.snapshot(settings, now=when).name == "stocksync-20260807-114500.db"

    def test_it_leaves_no_partial_file_behind(self, settings: Settings) -> None:
        backup.snapshot(settings)

        assert list(settings.backup_dir.glob("*.part")) == []

    def test_a_missing_database_is_an_error_not_an_empty_backup(self, tmp_path: Path) -> None:
        settings = Settings(
            database_url=f"sqlite+pysqlite:///{(tmp_path / 'gone.db').as_posix()}",
            backup_dir=tmp_path / "backups",
            env="test",
        )

        with pytest.raises(backup.BackupError, match="No database"):
            backup.snapshot(settings)

    def test_a_non_sqlite_url_says_so_rather_than_failing_obscurely(self, tmp_path: Path) -> None:
        settings = Settings(
            database_url="postgresql+psycopg://u:p@localhost:5432/stocksync",
            backup_dir=tmp_path / "backups",
            env="test",
        )

        with pytest.raises(backup.BackupError, match="does not point at a SQLite file"):
            backup.snapshot(settings)


class TestRetention:
    def test_it_keeps_the_newest_and_drops_the_rest(self, settings: Settings) -> None:
        for hour in range(20):
            backup.snapshot(settings, now=datetime(2026, 8, 7, hour, 0, 0))

        removed = backup.prune(settings)

        kept = sorted(p.name for p in settings.backup_dir.glob("stocksync-*.db"))
        assert len(kept) == settings.backup_keep
        assert len(removed) == 20 - settings.backup_keep
        # Newest kept, oldest gone — an age rule would delete a quiet
        # workspace's only snapshots precisely when nothing replaces them.
        assert kept[-1] == "stocksync-20260807-190000.db"

    def test_it_is_a_no_op_below_the_cap(self, settings: Settings) -> None:
        backup.snapshot(settings, now=datetime(2026, 8, 7, 1, 0, 0))

        assert backup.prune(settings) == []

    def test_a_missing_directory_is_not_an_error(self, settings: Settings) -> None:
        """Retention can run before the first snapshot ever has."""
        assert backup.prune(settings) == []

    def test_interrupted_snapshots_are_cleared_regardless_of_the_cap(
        self, settings: Settings
    ) -> None:
        """A `.part` is never a backup. Left alone it accumulates, and a restore
        script sorting by name could pick one as the newest."""
        backup.snapshot(settings)
        (settings.backup_dir / "stocksync-20260101-000000.db.part").write_bytes(b"half")

        backup.prune(settings)

        assert list(settings.backup_dir.glob("*.part")) == []


class TestRun:
    def test_it_snapshots_then_prunes(self, settings: Settings) -> None:
        for hour in range(settings.backup_keep + 3):
            backup.snapshot(settings, now=datetime(2026, 8, 6, hour, 0, 0))

        destination = backup.run(settings, now=datetime(2026, 8, 7, 9, 0, 0))

        assert destination.is_file()
        assert len(list(settings.backup_dir.glob("stocksync-*.db"))) == settings.backup_keep

    def test_latest_finds_the_newest(self, settings: Settings) -> None:
        backup.snapshot(settings, now=datetime(2026, 8, 6, 9, 0, 0))
        newest = backup.snapshot(settings, now=datetime(2026, 8, 7, 9, 0, 0))

        assert backup.latest(settings) == newest

    def test_latest_is_none_before_the_first_backup(self, settings: Settings) -> None:
        assert backup.latest(settings) is None
