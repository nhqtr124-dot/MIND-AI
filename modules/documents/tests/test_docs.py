from pathlib import Path

import pytest

from mind_docs import (
    DocumentSpec,
    PresentationSpec,
    WorkbookSpec,
    convert_with_libreoffice,
    extract_text,
    libreoffice_available,
    render_csv,
    render_document,
    render_pptx,
    render_xlsx,
    validate_csv,
    validate_document,
    validate_pptx,
    validate_xlsx,
)

SPEC = DocumentSpec.model_validate(
    {
        "title": "Robotics Team Report",
        "subtitle": "Season summary",
        "blocks": [
            {"type": "heading", "text": "Overview", "level": 1},
            {"type": "paragraph", "text": "The team built three robots and competed in two regional events this season."},
            {"type": "bullets", "items": ["Drive train redesign", "Vision tracking"]},
            {"type": "table", "columns": ["Event", "Rank"], "rows": [["Regional A", 4], ["Regional B", 2]]},
            {"type": "chart", "kind": "bar", "title": "Points", "labels": ["A", "B"], "series": [{"name": "pts", "values": [40, 55]}]},
            {"type": "heading", "text": "Next steps", "level": 2},
            {"type": "code", "text": "print('hello')", "language": "python"},
        ],
    }
)


@pytest.mark.parametrize("fmt", ["docx", "pdf", "md", "html", "txt"])
def test_documents_render_and_validate(fmt: str, tmp_path: Path) -> None:
    out = render_document(SPEC, fmt, tmp_path / f"report.{fmt}")
    v = validate_document(out, fmt, SPEC)
    assert v.passed, v.to_dict()
    if fmt in ("docx", "pdf", "md", "html", "txt"):
        assert "Robotics Team Report" in extract_text(out)


def test_validator_detects_wrong_content(tmp_path: Path) -> None:
    out = render_document(SPEC, "docx", tmp_path / "r.docx")
    other = SPEC.model_copy(update={"title": "Completely Different Title"})
    assert not validate_document(out, "docx", other).passed


def test_arabic_pdf_and_docx(tmp_path: Path) -> None:
    spec = DocumentSpec(title="تقرير الفريق", language="ar", blocks=[{"type": "heading", "text": "نظرة عامة", "level": 1}, {"type": "paragraph", "text": "بنى الفريق ثلاثة روبوتات هذا الموسم"}])  # type: ignore[list-item]
    for fmt in ("pdf", "docx"):
        out = render_document(spec, fmt, tmp_path / f"ar.{fmt}")
        v = validate_document(out, fmt, spec)
        assert v.passed, v.to_dict()


def test_pptx(tmp_path: Path) -> None:
    spec = PresentationSpec.model_validate(
        {
            "title": "Kickoff",
            "slides": [
                {"title": "Goals", "bullets": ["Win regional", "Document everything"], "notes": "speak slowly"},
                {"title": "Budget", "table": {"type": "table", "columns": ["Item", "Cost"], "rows": [["Motors", 120]]}},
                {"title": "Scores", "chart": {"type": "chart", "labels": ["Q1", "Q2"], "series": [{"name": "s", "values": [1, 2]}]}},
            ],
        }
    )
    out = render_pptx(spec, tmp_path / "deck.pptx")
    v = validate_pptx(out, spec)
    assert v.passed, v.to_dict()
    assert v.metrics["slides"] == 4


def _budget() -> WorkbookSpec:
    return WorkbookSpec.model_validate(
        {
            "sheets": [
                {
                    "name": "Budget",
                    "rows": [
                        ["Item", "Qty", "Unit", "Total"],
                        ["Motor", 4, 30, "=B2*C2"],
                        ["Battery", 2, 45.5, "=B3*C3"],
                        ["Frame", 1, 80, "=B4*C4"],
                        ["Sum", None, None, "=SUM(D2:D4)"],
                        ["Average", None, None, "=AVERAGE(D2:D4)"],
                    ],
                    "charts": [{"kind": "bar", "title": "Cost", "data_range": "D1:D4", "categories_range": "A2:A4"}],
                }
            ],
            "expected": {"Budget!D5": 291, "Budget!D2": 120},
        }
    )


def test_xlsx_formulas_validated(tmp_path: Path) -> None:
    spec = _budget()
    out = render_xlsx(spec, tmp_path / "b.xlsx")
    v = validate_xlsx(out, spec)
    assert v.passed, v.to_dict()
    assert v.metrics["formula_cells"] == 5
    assert v.metrics["charts"] == 1
    assert v.metrics["computed"]["Budget!D6"] == pytest.approx(97)


def test_xlsx_wrong_expectation_fails(tmp_path: Path) -> None:
    spec = _budget()
    spec.expected["Budget!D5"] = 300
    out = render_xlsx(spec, tmp_path / "b.xlsx")
    assert not validate_xlsx(out, spec).passed


def test_xlsx_error_value_fails(tmp_path: Path) -> None:
    spec = WorkbookSpec.model_validate({"sheets": [{"name": "S", "rows": [["a", "b"], [1, "=A2/0"]]}]})
    out = render_xlsx(spec, tmp_path / "e.xlsx")
    v = validate_xlsx(out, spec)
    assert not v.passed


def test_csv(tmp_path: Path) -> None:
    out = render_csv(["a", "b"], [[1, 2], [3, None]], tmp_path / "t.csv")
    assert validate_csv(out, ["a", "b"], 2).passed


@pytest.mark.skipif(not libreoffice_available(), reason="LibreOffice not installed")
def test_docx_to_pdf_conversion(tmp_path: Path) -> None:
    src = render_document(SPEC, "docx", tmp_path / "r.docx")
    pdf = convert_with_libreoffice(src, "pdf", tmp_path / "out")
    assert "Robotics Team Report" in extract_text(pdf)
