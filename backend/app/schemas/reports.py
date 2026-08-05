"""Request and response bodies for reports."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

#: The three report types and three formats of design doc §12.1, as patterns so
#: an unknown value is a 422 from the framework rather than a 500 further in.
KIND_PATTERN = "^(inventory|sales|sku_performance|dashboard|sku_matching)$"
FORMAT_PATTERN = "^(csv|xlsx|pdf)$"
#: The prototype's range control: three windows plus the Indian financial year.
RANGE_PATTERN = "^(7|30|90|180|365|fy)$"


class ReportColumn(BaseModel):
    header: str
    #: "left" or "right" — the client right-aligns figures without re-deriving
    #: which columns are numeric.
    align: str


class ReportPreview(BaseModel):
    """What the export will contain, before committing to building it."""

    title: str
    subtitle: str
    columns: list[ReportColumn]
    rows: list[list[str]]
    #: True when more rows exist than the preview shows.
    truncated: bool


class ReportCreate(BaseModel):
    kind: str = Field(pattern=KIND_PATTERN)
    fmt: str = Field(pattern=FORMAT_PATTERN)
    range_option: str = Field(default="30", pattern=RANGE_PATTERN)
    #: Off by default: an export is the copy people file and re-read, so it
    #: carries everything unless the top rows are what was actually asked for.
    top_only: bool = False


class ReportPayload(BaseModel):
    """One row of the Export Centre. Never carries the file itself."""

    id: int
    kind: str
    fmt: str
    status: str
    filename: str
    range_days: int | None
    range_label: str
    row_limit: int | None = None
    row_count: int
    size_bytes: int
    error_code: str | None
    error_detail: str | None
    created_at: datetime
    completed_at: datetime | None


class ReportPage(BaseModel):
    items: list[ReportPayload]
    total: int
    limit: int
    offset: int
