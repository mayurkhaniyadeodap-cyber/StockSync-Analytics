"""Exports on disk: the costs the BLOB did not have, and how each is paid.

Moving report bytes out of the database bought streaming downloads and a
database that stops growing with every export. It bought back the problems a
file store always has — orphans, half-written files, a row whose file is gone —
and these are the tests for each of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.models import Report
from app.services import report_store


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(export_dir=tmp_path / "exports", env="test")


def report(**overrides: object) -> Report:
    row = Report(
        workspace_id=7,
        kind="inventory",
        fmt="csv",
        status="ready",
        filename="stocksync-inventory-20260807-110000-3.csv",
    )
    row.id = 3
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


class TestTheStoredKey:
    def test_it_is_built_only_from_ids_and_the_format(self) -> None:
        """Nothing a user typed reaches the filesystem.

        `report.filename` is generated from user-visible text and is used in the
        Content-Disposition header. Keeping it out of the path is what makes
        traversal structurally impossible rather than a matter of sanitising.
        """
        assert report_store.relative_path(workspace_id=7, report_id=3, fmt="xlsx") == "7/3.xlsx"

    def test_it_is_relative_so_the_directory_can_move(self, settings: Settings) -> None:
        stored = report_store.write(settings, report=report(), content=b"a,b\n1,2\n")

        assert not Path(stored).is_absolute()
        assert report_store.absolute_path(settings, stored).is_file()


class TestWriting:
    def test_the_bytes_land_where_read_looks(self, settings: Settings) -> None:
        row = report()
        row.storage_path = report_store.write(settings, report=row, content=b"payload")

        assert report_store.read(settings, row).read_bytes() == b"payload"

    def test_it_creates_the_workspace_directory(self, settings: Settings) -> None:
        assert not settings.export_dir.exists()

        report_store.write(settings, report=report(), content=b"x")

        assert (settings.export_dir / "7").is_dir()

    def test_no_partial_file_is_left_under_the_final_name(self, settings: Settings) -> None:
        """Written to `.part` and renamed, so a reader never sees a truncated
        export — the rename is atomic within a filesystem."""
        row = report()
        row.storage_path = report_store.write(settings, report=row, content=b"complete")

        assert list(settings.export_dir.rglob("*.part")) == []

    def test_regenerating_overwrites_rather_than_accumulating(self, settings: Settings) -> None:
        row = report()
        report_store.write(settings, report=row, content=b"first")
        row.storage_path = report_store.write(settings, report=row, content=b"second")

        assert report_store.read(settings, row).read_bytes() == b"second"
        assert len(list((settings.export_dir / "7").iterdir())) == 1


class TestRemoving:
    def test_it_deletes_the_file(self, settings: Settings) -> None:
        row = report()
        row.storage_path = report_store.write(settings, report=row, content=b"x")

        report_store.remove(settings, row)

        assert not report_store.absolute_path(settings, row.storage_path).exists()

    def test_a_file_that_is_already_gone_is_not_an_error(self, settings: Settings) -> None:
        """The row is the record that matters. A delete that raised because the
        bytes had vanished would leave the user staring at a report they asked
        to be rid of."""
        row = report(storage_path="7/999.csv")

        report_store.remove(settings, row)  # must not raise

    def test_a_row_that_never_had_a_file_is_not_an_error(self, settings: Settings) -> None:
        report_store.remove(settings, report(storage_path=None))


class TestReading:
    def test_a_row_with_no_path_yields_nothing_readable(self, settings: Settings) -> None:
        """`download` turns this into the not-ready error rather than a 500 —
        from the user's side the export simply is not there."""
        assert not report_store.read(settings, report(storage_path=None)).is_file()

    def test_a_missing_file_is_reported_as_missing(self, settings: Settings) -> None:
        assert not report_store.read(settings, report(storage_path="7/404.csv")).is_file()


class TestSweepingOrphans:
    def test_it_removes_files_no_row_points_at(self, settings: Settings) -> None:
        kept = report_store.write(settings, report=report(), content=b"kept")
        orphan = report(workspace_id=7)
        orphan.id = 99
        report_store.write(settings, report=orphan, content=b"orphaned")

        removed = report_store.sweep_orphans(settings, {kept})

        assert removed == 1
        assert report_store.absolute_path(settings, kept).is_file()
        assert not (settings.export_dir / "7" / "99.csv").exists()

    def test_it_removes_interrupted_writes(self, settings: Settings) -> None:
        """A `.part` is a write that died mid-flight. No row will ever claim
        one, so it is always residue."""
        (settings.export_dir / "7").mkdir(parents=True)
        (settings.export_dir / "7" / "5.csv.part").write_bytes(b"half")

        assert report_store.sweep_orphans(settings, set()) == 1

    def test_a_missing_directory_is_not_an_error(self, settings: Settings) -> None:
        """First boot on a fresh deployment: nothing has been exported yet."""
        assert report_store.sweep_orphans(settings, set()) == 0

    def test_it_keeps_everything_that_is_still_referenced(self, settings: Settings) -> None:
        stored = [
            report_store.write(settings, report=report(), content=b"a"),
        ]
        second = report(workspace_id=9, fmt="xlsx")
        second.id = 4
        stored.append(report_store.write(settings, report=second, content=b"b"))

        assert report_store.sweep_orphans(settings, set(stored)) == 0
        assert all(report_store.absolute_path(settings, key).is_file() for key in stored)
