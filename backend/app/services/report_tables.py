"""The shape a report has, independent of who builds it or who writes it out.

Two dataclasses and nothing else. They live in their own module because both
``report_data`` (which builds most reports) and ``insights`` (which builds the
SKU performance one, so that the export and the screen cannot diverge) need
them, and ``report_data`` needs to call into ``insights``. With the dataclasses
defined in ``report_data`` that was a cycle, and the cycle was the only reason
the Reports centre kept a second, differently-sorted implementation of a table
the UI already knew how to build.

``report_data`` re-exports both names, so every existing import keeps working.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: "left" or "right". Alignment belongs to the column rather than to the value —
#: an empty numeric cell must still line up with the column above it.
Align = str


@dataclass(frozen=True)
class Column:
    header: str
    align: Align = "left"


@dataclass(frozen=True)
class ReportTable:
    title: str
    subtitle: str
    columns: tuple[Column, ...]
    # Sequence, not list: list is invariant, so a builder returning rows of a
    # known width could not be assigned to it without a redundant cast.
    rows: Sequence[Sequence[str]]
    #: True when the row cap cut the report short. Surfaced rather than hidden:
    #: an export that silently ends early is worse than no export.
    truncated: bool = False
