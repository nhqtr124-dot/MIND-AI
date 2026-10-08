from pathlib import Path

import numpy as np
import pytest
import trimesh

from mind_cad import TEMPLATES, CadError, generate_part, template_catalog, validate_mesh
from mind_cad.templates import UNO_HOLES


@pytest.mark.parametrize("key", list(TEMPLATES))
def test_every_template_exports_valid_stl_and_step(key: str, tmp_path: Path) -> None:
    res = generate_part(key, {}, tmp_path)
    stls = [f for f in res.files if f.format == "stl"]
    steps = [f for f in res.files if f.format == "step"]
    assert stls and len(stls) == len(steps)
    for f in res.files:
        assert Path(f.path).stat().st_size > 0
    for part in res.parts:
        assert part.brep_valid
        checks = {c["id"]: c["status"] for c in part.validation["checks"]}
        assert checks["watertight"] == "pass"
        assert checks["manifold"] == "pass"
        assert checks["dimensions"] == "pass"
    assert res.status != "validation_failed"
    assert (tmp_path / "report.json").exists()


def test_arduino_holder_matches_board_hole_pattern(tmp_path: Path) -> None:
    res = generate_part("arduino_uno_holder", {"margin": 5, "rim_height": 0, "mounting_holes": False}, tmp_path)
    stl = next(f.path for f in res.files if f.format == "stl")
    mesh = trimesh.load(stl, force="mesh")
    # The four screw holes must be empty space at the standoff centres just below the top.
    z = 3.0 + 6.0 - 0.5
    ox, oy = -68.6 / 2, -53.3 / 2
    centres = np.array([[ox + x, oy + y, z] for x, y in UNO_HOLES])
    assert not mesh.contains(centres).any()
    # ... while material surrounds them (point 2.2 mm from the centre, inside the 6 mm standoff wall)
    ring = centres + np.array([2.2, 0, 0])
    assert mesh.contains(ring).all()
    assert res.parts[0].validation["metrics"]["extents_mm"] == pytest.approx([78.6, 63.3, 9.0], abs=0.01)


def test_requested_dimensions_drive_geometry(tmp_path: Path) -> None:
    res = generate_part("l_bracket", {"width": 25, "leg_a": 60, "leg_b": 45, "thickness": 5}, tmp_path)
    assert res.parts[0].validation["metrics"]["extents_mm"] == pytest.approx([60, 25, 45], abs=0.01)


def test_invalid_parameters_rejected(tmp_path: Path) -> None:
    with pytest.raises(CadError):
        generate_part("standoff", {"outer_diameter": 4, "hole_diameter": 3.5}, tmp_path)
    with pytest.raises(CadError):
        generate_part("nope", {}, tmp_path)


def test_open_mesh_fails_validation(tmp_path: Path) -> None:
    box = trimesh.creation.box(extents=(20, 20, 20))
    broken = trimesh.Trimesh(vertices=box.vertices, faces=box.faces[:-2], process=False)
    path = tmp_path / "broken.stl"
    broken.export(path)
    report = validate_mesh(path)
    assert report.status == "validation_failed"
    assert not report.passed
    assert {c.id: c.status for c in report.checks}["watertight"] == "fail"


def test_dimension_mismatch_fails(tmp_path: Path) -> None:
    path = tmp_path / "box.stl"
    trimesh.creation.box(extents=(10, 10, 10)).export(path)
    report = validate_mesh(path, expected_extents=(10, 10, 12))
    assert report.status == "validation_failed"


def test_missing_and_garbage_files_fail(tmp_path: Path) -> None:
    assert validate_mesh(tmp_path / "missing.stl").status == "validation_failed"
    junk = tmp_path / "junk.stl"
    junk.write_bytes(b"not an stl at all")
    assert validate_mesh(junk).status == "validation_failed"


def test_thin_wall_warns(tmp_path: Path) -> None:
    path = tmp_path / "thin.stl"
    trimesh.creation.box(extents=(40, 40, 0.4)).export(path)
    report = validate_mesh(path)
    assert {c.id: c.status for c in report.checks}["wall_thickness"] == "warn"
    assert report.status == "printable_with_warnings"


def test_catalog_has_json_schemas() -> None:
    cat = template_catalog()
    assert {c["key"] for c in cat} == set(TEMPLATES)
    assert all("properties" in c["params_schema"] for c in cat)


def test_3mf_export(tmp_path: Path) -> None:
    res = generate_part("standoff", {}, tmp_path, formats=("stl", "3mf"))
    assert any(f.format == "3mf" for f in res.files)
