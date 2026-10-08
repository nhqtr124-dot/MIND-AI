"""Mesh validation for STL/3MF outputs.

Each check reports ``pass``, ``warn``, ``fail`` or ``not_checked`` with evidence.
The overall status is never "print ready" if any critical check failed, and the
report always states that passing mesh checks does not guarantee a good print.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
import trimesh

CheckStatus = Literal["pass", "warn", "fail", "not_checked"]
OverallStatus = Literal["print_ready_checks_passed", "printable_with_warnings", "validation_failed"]

DISCLAIMER = (
    "Passing these mesh checks does not guarantee a successful print. Slicer settings, material, "
    "printer calibration and part orientation all affect the result."
)


@dataclass
class Check:
    id: str
    label: str
    status: CheckStatus
    detail: str
    critical: bool = True


@dataclass
class ValidationReport:
    file: str
    status: OverallStatus
    checks: list[Check]
    metrics: dict[str, float | int | list[float]] = field(default_factory=dict)
    disclaimer: str = DISCLAIMER

    @property
    def passed(self) -> bool:
        return self.status != "validation_failed"

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["passed"] = self.passed
        return d


@dataclass
class PrintSettings:
    nozzle_diameter: float = 0.4
    min_wall: float = 0.8  # two perimeters with a 0.4 mm nozzle
    max_overhang_deg: float = 45.0
    build_volume: tuple[float, float, float] = (220.0, 220.0, 250.0)


def _wall_thickness_samples(mesh: trimesh.Trimesh, n: int = 600, seed: int = 7) -> np.ndarray:
    """Estimate local wall thickness by casting rays inward from sampled surface points."""
    points, face_idx = trimesh.sample.sample_surface(mesh, n, seed=seed)
    if len(points) == 0:
        return np.array([])
    normals = mesh.face_normals[face_idx]
    origins = points - normals * 1e-4
    locs, ray_idx, _ = mesh.ray.intersects_location(origins, -normals, multiple_hits=False)
    if len(ray_idx) == 0:
        return np.array([])
    return np.linalg.norm(locs - origins[ray_idx], axis=1)


def validate_mesh(
    path: str | Path,
    expected_extents: tuple[float, float, float] | None = None,
    tolerance: float = 0.5,
    require_watertight: bool = True,
    settings: PrintSettings | None = None,
) -> ValidationReport:
    settings = settings or PrintSettings()
    path = Path(path)
    checks: list[Check] = []
    metrics: dict[str, float | int | list[float]] = {}

    def finish() -> ValidationReport:
        failed = any(c.status == "fail" and c.critical for c in checks)
        warned = any(c.status in ("warn", "fail") for c in checks)
        status: OverallStatus = (
            "validation_failed"
            if failed
            else "printable_with_warnings"
            if warned
            else "print_ready_checks_passed"
        )
        return ValidationReport(file=path.name, status=status, checks=checks, metrics=metrics)

    # 1. existence
    if not path.is_file() or path.stat().st_size == 0:
        checks.append(Check("exists", "File exists", "fail", f"{path.name} is missing or empty"))
        return finish()
    checks.append(Check("exists", "File exists", "pass", f"{path.stat().st_size} bytes"))

    # 2. loading
    try:
        loaded = trimesh.load(path, force="mesh", process=True)
    except Exception as exc:  # noqa: BLE001 - any loader failure is a validation failure
        checks.append(Check("load", "Mesh loads", "fail", f"loader error: {exc}"))
        return finish()
    if not isinstance(loaded, trimesh.Trimesh):
        checks.append(Check("load", "Mesh loads", "fail", f"unexpected type {type(loaded).__name__}"))
        return finish()
    mesh = loaded
    checks.append(
        Check("load", "Mesh loads", "pass", f"{len(mesh.vertices)} vertices, {len(mesh.faces)} faces")
    )

    # 3. nonempty
    if len(mesh.faces) == 0:
        checks.append(Check("nonempty", "Geometry is non-empty", "fail", "no faces"))
        return finish()
    checks.append(Check("nonempty", "Geometry is non-empty", "pass", f"{len(mesh.faces)} faces"))

    # 4. units / bounding dimensions
    extents = [float(round(v, 4)) for v in mesh.extents]
    metrics["extents_mm"] = extents
    if expected_extents is not None:
        diffs = [abs(a - b) for a, b in zip(extents, expected_extents, strict=True)]
        ok = all(d <= tolerance for d in diffs)
        checks.append(
            Check(
                "dimensions",
                "Bounding dimensions match the design",
                "pass" if ok else "fail",
                f"measured {extents} mm vs expected {[round(v, 3) for v in expected_extents]} mm (tolerance {tolerance} mm)",
            )
        )
    else:
        largest = max(extents)
        if largest < 2:
            checks.append(
                Check(
                    "dimensions",
                    "Plausible units",
                    "warn",
                    f"largest extent {largest} — file may be in metres or inches",
                    False,
                )
            )
        elif largest > 2000:
            checks.append(
                Check(
                    "dimensions",
                    "Plausible units",
                    "warn",
                    f"largest extent {largest} — file may be in microns",
                    False,
                )
            )
        else:
            checks.append(
                Check(
                    "dimensions",
                    "Plausible units",
                    "pass",
                    f"extents {extents}, interpreted as millimetres",
                    False,
                )
            )
    bv = settings.build_volume
    fits = all(e <= b for e, b in zip(sorted(extents), sorted(bv), strict=True))
    checks.append(
        Check(
            "build_volume",
            "Fits build volume",
            "pass" if fits else "warn",
            f"build volume {list(bv)} mm",
            critical=False,
        )
    )

    # 5. watertight
    watertight = bool(mesh.is_watertight)
    checks.append(
        Check(
            "watertight",
            "Watertight (closed surface)",
            "pass" if watertight else ("fail" if require_watertight else "warn"),
            "every edge is shared by exactly two faces" if watertight else "open edges or holes detected",
            critical=require_watertight,
        )
    )

    # 6. manifold (independent check with the manifold3d kernel)
    try:
        import manifold3d

        m = manifold3d.Manifold(
            manifold3d.Mesh(
                vert_properties=np.asarray(mesh.vertices, dtype=np.float32),
                tri_verts=np.asarray(mesh.faces, dtype=np.uint32),
            )
        )
        st = m.status()
        ok = st == manifold3d.Error.NoError
        metrics["genus"] = int(m.genus()) if ok else -1
        checks.append(
            Check("manifold", "Manifold geometry", "pass" if ok else "fail", f"manifold3d status: {st.name}")
        )
    except ImportError:
        checks.append(
            Check("manifold", "Manifold geometry", "not_checked", "manifold3d not installed", False)
        )

    # 7. degenerate faces
    areas = mesh.area_faces
    degenerate = int(np.sum(areas < 1e-10))
    checks.append(
        Check(
            "degenerate",
            "No degenerate triangles",
            "pass" if degenerate == 0 else "warn",
            f"{degenerate} zero-area triangles",
            critical=False,
        )
    )

    # 8. normals / winding
    winding = bool(mesh.is_winding_consistent)
    volume = float(mesh.volume) if watertight else float("nan")
    outward = watertight and volume > 0
    normals_ok = winding and (outward or not watertight)
    checks.append(
        Check(
            "normals",
            "Consistent outward normals",
            "pass" if normals_ok and watertight else ("warn" if winding else "fail"),
            f"winding consistent: {winding}; signed volume: {volume:.3f} mm^3"
            if watertight
            else f"winding consistent: {winding}",
        )
    )
    if watertight:
        metrics["volume_mm3"] = round(abs(volume), 3)
    metrics["surface_area_mm2"] = round(float(mesh.area), 3)
    metrics["bodies"] = len(mesh.split(only_watertight=False))

    # 9. self-intersection: no reliable mesh-level check in the stack; state that.
    checks.append(
        Check(
            "self_intersection",
            "Self-intersections",
            "not_checked",
            "No mesh-level self-intersection test is available; generated B-rep solids are checked separately.",
            critical=False,
        )
    )

    # 10. printability heuristics
    if watertight and len(mesh.faces) > 0:
        t = _wall_thickness_samples(mesh)
        if len(t):
            p2 = float(np.percentile(t, 2))
            metrics["wall_thickness_min_mm"] = round(float(t.min()), 3)
            metrics["wall_thickness_p2_mm"] = round(p2, 3)
            status: CheckStatus = "pass" if p2 >= settings.min_wall else "warn"
            checks.append(
                Check(
                    "wall_thickness",
                    "Wall thickness",
                    status,
                    f"2nd percentile {p2:.2f} mm (min {t.min():.2f} mm) vs minimum {settings.min_wall} mm; ray-sampled estimate",
                    critical=False,
                )
            )
        z_min = mesh.bounds[0][2]
        down = mesh.face_normals[:, 2]
        centroids_z = mesh.triangles_center[:, 2]
        limit = -math.cos(math.radians(90 - settings.max_overhang_deg))
        on_bed = centroids_z <= z_min + 0.05
        overhang = (down < limit) & ~on_bed
        frac = float(areas[overhang].sum() / max(areas.sum(), 1e-9))
        bed_area = float(areas[on_bed & (down < -0.99)].sum())
        metrics["overhang_area_fraction"] = round(frac, 4)
        metrics["bed_contact_area_mm2"] = round(bed_area, 2)
        checks.append(
            Check(
                "overhangs",
                f"Overhangs steeper than {settings.max_overhang_deg} deg",
                "pass" if frac < 0.02 else "warn",
                f"{frac * 100:.1f}% of surface area in the current orientation; supports may be needed"
                if frac >= 0.02
                else f"{frac * 100:.1f}% of surface area",
                critical=False,
            )
        )
        height = float(mesh.extents[2])
        stable = bed_area >= 10 and height <= 4 * math.sqrt(max(bed_area, 1e-9))
        checks.append(
            Check(
                "bed_contact",
                "Flat bed contact",
                "pass" if stable else "warn",
                f"{bed_area:.1f} mm^2 flat area on the build plate for a {height:.1f} mm tall part"
                + ("" if stable else "; small footprint for the height, consider a brim or reorienting"),
                critical=False,
            )
        )
    else:
        checks.append(
            Check("wall_thickness", "Wall thickness", "not_checked", "requires a watertight mesh", False)
        )
    return finish()
