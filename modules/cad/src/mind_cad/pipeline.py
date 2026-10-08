"""Generate -> export -> validate pipeline for parametric parts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cadquery as cq
import trimesh

from .templates import TEMPLATES, TemplateResult
from .validation import PrintSettings, ValidationReport, validate_mesh

PLA_DENSITY_G_CM3 = 1.24


@dataclass
class ExportedFile:
    part: str
    format: str
    path: str
    size_bytes: int


@dataclass
class PartReport:
    name: str
    brep_valid: bool
    expected_extents: list[float]
    validation: dict[str, Any]


@dataclass
class CadResult:
    template: str
    params: dict[str, Any]
    files: list[ExportedFile]
    parts: list[PartReport]
    bom: list[dict[str, Any]]
    assumptions: list[str]
    status: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CadError(ValueError):
    pass


def _export_3mf(stl_path: Path, out: Path) -> bool:
    try:
        mesh = trimesh.load(stl_path, force="mesh")
        out.write_bytes(trimesh.exchange.threemf.export_3MF(mesh))
        return out.stat().st_size > 0
    except Exception:  # noqa: BLE001 - 3MF is optional; caller records the warning
        return False


def generate_part(
    template_key: str,
    params: dict[str, Any],
    out_dir: str | Path,
    formats: tuple[str, ...] = ("stl", "step"),
    settings: PrintSettings | None = None,
) -> CadResult:
    template = TEMPLATES.get(template_key)
    if template is None:
        raise CadError(f"unknown template '{template_key}'")
    try:
        p = template.Params.model_validate(params)
    except Exception as exc:
        raise CadError(str(exc)) from exc
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    result: TemplateResult = template.build(p)
    files: list[ExportedFile] = []
    parts: list[PartReport] = []
    warnings: list[str] = []
    bom: list[dict[str, Any]] = []

    for part in result.parts:
        solid = part.shape.val()
        brep_valid = bool(solid.isValid())
        stl = out / f"{part.name}.stl"
        cq.exporters.export(part.shape, str(stl), exportType="STL", tolerance=0.02, angularTolerance=0.1)
        files.append(ExportedFile(part.name, "stl", str(stl), stl.stat().st_size))
        if "step" in formats:
            step = out / f"{part.name}.step"
            cq.exporters.export(part.shape, str(step), exportType="STEP")
            files.append(ExportedFile(part.name, "step", str(step), step.stat().st_size))
        if "3mf" in formats:
            tmf = out / f"{part.name}.3mf"
            if _export_3mf(stl, tmf):
                files.append(ExportedFile(part.name, "3mf", str(tmf), tmf.stat().st_size))
            else:
                warnings.append(f"3MF export failed for {part.name}")

        report: ValidationReport = validate_mesh(
            stl, expected_extents=part.expected_extents, require_watertight=part.needs_watertight, settings=settings
        )
        report.checks.insert(
            0,
            type(report.checks[0])(
                "brep_valid",
                "B-rep solid valid (OpenCascade BRepCheck)",
                "pass" if brep_valid else "fail",
                "solid passed BRepCheck_Analyzer" if brep_valid else "solid failed BRepCheck_Analyzer",
            ),
        )
        if not brep_valid:
            report.status = "validation_failed"
        parts.append(PartReport(part.name, brep_valid, [round(v, 4) for v in part.expected_extents], report.to_dict()))
        vol = report.metrics.get("volume_mm3")
        bom.append(
            {
                "item": part.name,
                "type": "printed part",
                "quantity": 1,
                "volume_cm3": round(float(vol) / 1000, 2) if isinstance(vol, (int, float)) else None,
                "est_mass_g_pla_solid": round(float(vol) / 1000 * PLA_DENSITY_G_CM3, 1) if isinstance(vol, (int, float)) else None,
            }
        )

    for hw in result.hardware:
        bom.append({"item": hw.name, "type": "hardware", "quantity": hw.quantity, "note": hw.note})

    statuses = [pr.validation["status"] for pr in parts]
    status = (
        "validation_failed"
        if "validation_failed" in statuses
        else "printable_with_warnings"
        if "printable_with_warnings" in statuses
        else "print_ready_checks_passed"
    )
    assumptions = [
        *result.assumptions,
        "All dimensions are millimetres.",
        f"Mass estimates assume 100% infill PLA at {PLA_DENSITY_G_CM3} g/cm^3; real prints with infill weigh less.",
    ]
    res = CadResult(
        template=template_key,
        params=p.model_dump(),
        files=files,
        parts=parts,
        bom=bom,
        assumptions=assumptions,
        status=status,
        warnings=warnings,
    )
    (out / "report.json").write_text(json.dumps(res.to_dict(), indent=2, default=str))
    return res
