"""Structured specifications that generators render into real files.

An LLM (or a person, through the UI) produces a spec; renderers turn it into a
file; validators reopen the file and check it against the same spec.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator


class Heading(BaseModel):
    type: Literal["heading"] = "heading"
    text: str = Field(min_length=1)
    level: int = Field(1, ge=1, le=4)


class Paragraph(BaseModel):
    type: Literal["paragraph"] = "paragraph"
    text: str


class BulletList(BaseModel):
    type: Literal["bullets"] = "bullets"
    items: list[str] = Field(min_length=1)
    ordered: bool = False


class Table(BaseModel):
    type: Literal["table"] = "table"
    columns: list[str] = Field(min_length=1)
    rows: list[list[Any]]
    caption: str | None = None

    @field_validator("rows")
    @classmethod
    def _width(cls, rows: list[list[Any]], info: Any) -> list[list[Any]]:
        cols = info.data.get("columns") or []
        for r in rows:
            if len(r) != len(cols):
                raise ValueError(f"row has {len(r)} cells, expected {len(cols)}")
        return rows


class ChartSeries(BaseModel):
    name: str
    values: list[float]


class Chart(BaseModel):
    type: Literal["chart"] = "chart"
    kind: Literal["bar", "line", "pie"] = "bar"
    title: str = ""
    labels: list[str] = Field(min_length=1)
    series: list[ChartSeries] = Field(min_length=1)

    @field_validator("series")
    @classmethod
    def _len(cls, series: list[ChartSeries], info: Any) -> list[ChartSeries]:
        n = len(info.data.get("labels") or [])
        for s in series:
            if len(s.values) != n:
                raise ValueError(f"series '{s.name}' has {len(s.values)} values for {n} labels")
        return series


class Code(BaseModel):
    type: Literal["code"] = "code"
    text: str
    language: str = ""


class PageBreak(BaseModel):
    type: Literal["page_break"] = "page_break"


Block = Annotated[
    Heading | Paragraph | BulletList | Table | Chart | Code | PageBreak, Field(discriminator="type")
]


class DocumentSpec(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    subtitle: str | None = None
    author: str | None = None
    language: str = "en"
    blocks: list[Block] = Field(default_factory=list)


class Slide(BaseModel):
    title: str
    bullets: list[str] = Field(default_factory=list)
    notes: str | None = None
    table: Table | None = None
    chart: Chart | None = None


class PresentationSpec(BaseModel):
    title: str = Field(min_length=1)
    subtitle: str | None = None
    slides: list[Slide] = Field(min_length=1)


CellValue = str | int | float | bool | None


class SheetSpec(BaseModel):
    name: str = Field(min_length=1, max_length=31)
    rows: list[list[CellValue]] = Field(default_factory=list)
    column_widths: dict[str, float] = Field(default_factory=dict)
    bold_header: bool = True
    charts: list[SheetChart] = Field(default_factory=list)


class SheetChart(BaseModel):
    kind: Literal["bar", "line"] = "bar"
    title: str = ""
    data_range: str = Field(description="e.g. B1:C6 including header row")
    categories_range: str = Field(description="e.g. A2:A6")
    anchor: str = "E2"


class WorkbookSpec(BaseModel):
    sheets: list[SheetSpec] = Field(min_length=1)
    expected: dict[str, float | int | str | bool] = Field(
        default_factory=dict, description="Expected computed values, keyed 'Sheet!A1'"
    )


SheetSpec.model_rebuild()

DocFormat = Literal["docx", "pdf", "md", "html", "txt"]
