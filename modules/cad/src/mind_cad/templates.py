"""Parametric part templates built with CadQuery (OpenCascade B-rep kernel).

Every template is dimension-driven: parameters are validated with Pydantic, all
lengths are millimetres, and the template returns one or more named solids plus
the bounding box it intends to produce so validation can compare intent with the
exported mesh.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

import cadquery as cq
from pydantic import BaseModel, Field, model_validator


@dataclass
class HardwareItem:
    name: str
    quantity: int
    note: str = ""


@dataclass
class PartSolid:
    name: str
    shape: cq.Workplane
    expected_extents: tuple[float, float, float]
    needs_watertight: bool = True


@dataclass
class TemplateResult:
    parts: list[PartSolid]
    hardware: list[HardwareItem] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


class CadTemplate:
    key: ClassVar[str]
    title: ClassVar[str]
    description: ClassVar[str]
    Params: ClassVar[type[BaseModel]]

    def build(self, params: BaseModel) -> TemplateResult:  # pragma: no cover - interface
        raise NotImplementedError


# --------------------------------------------------------------------------- Arduino UNO holder

# Arduino UNO R3 board outline and mounting holes (mm), measured from the lower-left
# board corner, per the published Arduino UNO R3 mechanical drawing.
UNO_BOARD_L = 68.6
UNO_BOARD_W = 53.3
UNO_HOLES: tuple[tuple[float, float], ...] = ((13.97, 2.54), (15.24, 50.8), (66.04, 7.62), (66.04, 35.56))


class ArduinoUnoHolderParams(BaseModel):
    base_thickness: float = Field(3.0, ge=1.2, le=10)
    margin: float = Field(5.0, ge=2, le=30, description="Base extension beyond the board edge")
    standoff_height: float = Field(6.0, ge=2, le=30)
    standoff_outer_diameter: float = Field(6.0, ge=4, le=12)
    screw_hole_diameter: float = Field(2.8, ge=1.5, le=4.0, description="2.8 mm suits M3 self-tapping")
    rim_height: float = Field(0.0, ge=0, le=25, description="0 disables the rim wall")
    rim_thickness: float = Field(2.0, ge=1.2, le=6)
    corner_radius: float = Field(3.0, ge=0, le=10)
    mounting_holes: bool = Field(True, description="Countersunk holes in the base for fixing the holder")
    mounting_hole_diameter: float = Field(3.4, ge=2, le=6)

    @model_validator(mode="after")
    def _check(self) -> ArduinoUnoHolderParams:
        if self.standoff_outer_diameter - self.screw_hole_diameter < 1.6:
            raise ValueError("standoff wall would be thinner than 0.8 mm")
        if self.rim_height > 0 and self.margin < self.rim_thickness + 1.0:
            raise ValueError("margin must leave at least 1 mm between rim and board")
        return self


class ArduinoUnoHolder(CadTemplate):
    key = "arduino_uno_holder"
    title = "Arduino UNO holder"
    description = "Base plate with four M3 standoffs matching the Arduino UNO R3 hole pattern."
    Params = ArduinoUnoHolderParams

    def build(self, params: BaseModel) -> TemplateResult:
        p = ArduinoUnoHolderParams.model_validate(params.model_dump())
        length = UNO_BOARD_L + 2 * p.margin
        width = UNO_BOARD_W + 2 * p.margin
        base = cq.Workplane("XY").box(length, width, p.base_thickness, centered=(True, True, False))
        if p.corner_radius > 0:
            base = base.edges("|Z").fillet(min(p.corner_radius, p.margin - 0.5))

        # Board origin in model coordinates (board lower-left corner).
        ox, oy = -UNO_BOARD_L / 2, -UNO_BOARD_W / 2
        pts = [(ox + x, oy + y) for x, y in UNO_HOLES]
        standoffs = (
            cq.Workplane("XY")
            .workplane(offset=p.base_thickness)
            .pushPoints(pts)
            .circle(p.standoff_outer_diameter / 2)
            .extrude(p.standoff_height)
        )
        body = base.union(standoffs)
        body = (
            body.faces(">Z")
            .workplane(origin=(0, 0, p.base_thickness + p.standoff_height))
            .pushPoints(pts)
            .hole(p.screw_hole_diameter, depth=p.standoff_height + p.base_thickness - 1.0)
        )

        height = p.base_thickness + p.standoff_height
        if p.rim_height > 0:
            outer = cq.Workplane("XY").workplane(offset=p.base_thickness).rect(length, width).extrude(p.rim_height)
            inner = (
                cq.Workplane("XY")
                .workplane(offset=p.base_thickness)
                .rect(length - 2 * p.rim_thickness, width - 2 * p.rim_thickness)
                .extrude(p.rim_height)
            )
            rim = outer.cut(inner)
            # USB-B and barrel jack overhang the x=0 board edge: leave that side open.
            opening = (
                cq.Workplane("XY")
                .workplane(offset=p.base_thickness)
                .center(-length / 2 + p.rim_thickness / 2, 0)
                .rect(p.rim_thickness * 2, UNO_BOARD_W - 4)
                .extrude(p.rim_height)
            )
            rim = rim.cut(opening)
            body = body.union(rim)
            height = max(height, p.base_thickness + p.rim_height)

        if p.mounting_holes:
            inset = p.margin / 2
            mpts = [
                (-length / 2 + inset, -width / 2 + inset),
                (length / 2 - inset, -width / 2 + inset),
                (-length / 2 + inset, width / 2 - inset),
                (length / 2 - inset, width / 2 - inset),
            ]
            body = body.faces("<Z").workplane().pushPoints([(x, -y) for x, y in mpts]).hole(p.mounting_hole_diameter)

        assumptions = [
            "Hole pattern from the Arduino UNO R3 mechanical drawing (board 68.6 x 53.3 mm).",
            f"Screw holes are {p.screw_hole_diameter} mm for M3 self-tapping screws; use 3.2 mm for M3 machine screws with nuts.",
            "USB-B and DC jack side (x-) is left open when a rim is enabled.",
        ]
        if p.mounting_holes and p.mounting_hole_diameter > p.margin - 1:
            assumptions.append("Mounting holes are large relative to the margin; check edge distance.")
        return TemplateResult(
            parts=[PartSolid("arduino_uno_holder", body, (length, width, height))],
            hardware=[
                HardwareItem("M3 x 6 mm self-tapping screw", 4, "board to standoffs"),
                *([HardwareItem("M3 screw (length to suit mounting surface)", 4, "holder to surface")] if p.mounting_holes else []),
            ],
            assumptions=assumptions,
        )


# --------------------------------------------------------------------------- Vented enclosure


class VentedEnclosureParams(BaseModel):
    inner_length: float = Field(80, ge=10, le=400)
    inner_width: float = Field(50, ge=10, le=400)
    inner_height: float = Field(30, ge=5, le=300)
    wall: float = Field(2.0, ge=1.2, le=8)
    floor: float = Field(2.0, ge=1.2, le=8)
    lid_thickness: float = Field(2.0, ge=1.2, le=8)
    lid_lip_depth: float = Field(3.0, ge=0, le=15)
    lid_clearance: float = Field(0.3, ge=0.1, le=1.0, description="Per-side gap between lid lip and walls")
    vent_slots: int = Field(6, ge=0, le=40, description="Slots per long side")
    vent_width: float = Field(2.0, ge=1.0, le=10)
    vent_height_ratio: float = Field(0.5, gt=0, le=0.8)
    cable_hole_diameter: float = Field(0.0, ge=0, le=30, description="0 disables the cable hole on a short side")

    @model_validator(mode="after")
    def _check(self) -> VentedEnclosureParams:
        if self.vent_slots:
            pitch = self.inner_length / (self.vent_slots + 1)
            if pitch - self.vent_width < 1.2:
                raise ValueError("vent slots are too dense: less than 1.2 mm material between slots")
        if self.cable_hole_diameter and self.cable_hole_diameter > self.inner_height - 2:
            raise ValueError("cable hole does not fit in the wall height")
        return self


def _vented_enclosure(p: VentedEnclosureParams) -> tuple[PartSolid, PartSolid]:
    ol = p.inner_length + 2 * p.wall
    ow = p.inner_width + 2 * p.wall
    oh = p.inner_height + p.floor
    body = cq.Workplane("XY").box(ol, ow, oh, centered=(True, True, False))
    cavity = (
        cq.Workplane("XY")
        .workplane(offset=p.floor)
        .box(p.inner_length, p.inner_width, p.inner_height + 1, centered=(True, True, False))
    )
    body = body.cut(cavity)

    if p.vent_slots:
        slot_h = p.inner_height * p.vent_height_ratio
        z_center = p.floor + p.inner_height * 0.45
        pitch = p.inner_length / (p.vent_slots + 1)
        xs = [-p.inner_length / 2 + pitch * (i + 1) for i in range(p.vent_slots)]
        for side in (1, -1):
            for x in xs:
                slot = cq.Workplane("XY").box(p.vent_width, p.wall * 3, slot_h).translate(
                    (x, side * (p.inner_width / 2 + p.wall / 2), z_center)
                )
                body = body.cut(slot)

    if p.cable_hole_diameter:
        hole = (
            cq.Workplane("YZ")
            .circle(p.cable_hole_diameter / 2)
            .extrude(p.wall * 3)
            .translate((p.inner_length / 2 - p.wall, 0, p.floor + p.inner_height / 2))
        )
        body = body.cut(hole)

    lid = cq.Workplane("XY").box(ol, ow, p.lid_thickness, centered=(True, True, False))
    if p.lid_lip_depth > 0:
        lip_l = p.inner_length - 2 * p.lid_clearance
        lip_w = p.inner_width - 2 * p.lid_clearance
        lip = (
            cq.Workplane("XY")
            .workplane(offset=p.lid_thickness)
            .rect(lip_l, lip_w)
            .rect(lip_l - 2 * p.wall, lip_w - 2 * p.wall)
            .extrude(p.lid_lip_depth)
        )
        lid = lid.union(lip)
    return (
        PartSolid("enclosure_body", body, (ol, ow, oh)),
        PartSolid("enclosure_lid", lid, (ol, ow, p.lid_thickness + p.lid_lip_depth)),
    )


class VentedEnclosure(CadTemplate):
    key = "vented_enclosure"
    title = "Vented enclosure with lid"
    description = "Rectangular box with ventilation slots on both long sides and a press-fit lid."
    Params = VentedEnclosureParams

    def build(self, params: BaseModel) -> TemplateResult:
        p = VentedEnclosureParams.model_validate(params.model_dump())
        body, lid = _vented_enclosure(p)
        return TemplateResult(
            parts=[body, lid],
            assumptions=[
                f"Lid lip clearance {p.lid_clearance} mm per side; tune for your printer's tolerance.",
                "Print the lid upside down (flat face on the bed).",
            ],
        )


class BatteryEnclosureParams(BaseModel):
    cell_diameter: float = Field(18.6, ge=5, le=60, description="18.6 mm suits 18650 cells")
    cell_length: float = Field(65.5, ge=10, le=200)
    cells_across: int = Field(2, ge=1, le=12)
    cell_rows: int = Field(1, ge=1, le=8)
    clearance: float = Field(1.5, ge=0, le=10, description="Space around the pack for wiring and holder")
    wall: float = Field(2.4, ge=1.2, le=8)
    vent_slots: int = Field(5, ge=0, le=30)
    cable_hole_diameter: float = Field(6.0, ge=0, le=20)


class BatteryEnclosure(CadTemplate):
    key = "battery_enclosure"
    title = "Battery enclosure with ventilation"
    description = "Vented enclosure sized from cylindrical cell dimensions and count."
    Params = BatteryEnclosureParams

    def build(self, params: BaseModel) -> TemplateResult:
        p = BatteryEnclosureParams.model_validate(params.model_dump())
        inner = VentedEnclosureParams(
            inner_length=p.cell_length + 2 * p.clearance + 4,  # 2 mm per end for contacts/springs
            inner_width=p.cell_diameter * p.cells_across + 2 * p.clearance,
            inner_height=p.cell_diameter * p.cell_rows + 2 * p.clearance,
            wall=p.wall,
            floor=p.wall,
            lid_thickness=p.wall,
            vent_slots=p.vent_slots,
            cable_hole_diameter=p.cable_hole_diameter,
        )
        body, lid = _vented_enclosure(inner)
        return TemplateResult(
            parts=[body, lid],
            hardware=[HardwareItem(f"{p.cells_across * p.cell_rows}-cell holder or pack", 1, "not modelled")],
            assumptions=[
                "Cavity is sized from nominal cell dimensions plus clearance and 2 mm per end for contacts.",
                "Ventilation slots do not make an enclosure safe for charging lithium cells; follow the cell maker's guidance.",
                "PLA softens around 55-60 C; use PETG or ASA if the pack gets warm.",
            ],
        )


# --------------------------------------------------------------------------- L bracket


class LBracketParams(BaseModel):
    width: float = Field(20, ge=5, le=200)
    leg_a: float = Field(40, ge=8, le=300)
    leg_b: float = Field(30, ge=8, le=300)
    thickness: float = Field(4, ge=1.5, le=20)
    hole_diameter: float = Field(4.3, ge=0, le=20, description="0 disables holes; 4.3 mm clears M4")
    holes_per_leg: int = Field(1, ge=0, le=6)
    gusset: bool = True

    @model_validator(mode="after")
    def _check(self) -> LBracketParams:
        if self.holes_per_leg and self.hole_diameter >= self.width - 2:
            raise ValueError("hole diameter leaves less than 1 mm of material each side")
        return self


class LBracket(CadTemplate):
    key = "l_bracket"
    title = "Mounting L-bracket"
    description = "90 degree bracket with bolt holes in both legs and an optional gusset."
    Params = LBracketParams

    def build(self, params: BaseModel) -> TemplateResult:
        p = LBracketParams.model_validate(params.model_dump())
        horiz = cq.Workplane("XY").box(p.leg_a, p.width, p.thickness, centered=(False, True, False))
        vert = cq.Workplane("XY").box(p.thickness, p.width, p.leg_b, centered=(False, True, False))
        body = horiz.union(vert)
        if p.gusset:
            g = min(p.leg_a, p.leg_b) * 0.45
            gw = max(p.thickness, 2.0)
            gusset = (
                cq.Workplane("XZ")
                .polyline([(p.thickness, p.thickness), (p.thickness + g, p.thickness), (p.thickness, p.thickness + g)])
                .close()
                .extrude(gw / 2, both=True)
            )
            body = body.union(gusset)
        if p.holes_per_leg and p.hole_diameter > 0:
            start = p.thickness + (p.width * 0.5 if p.gusset else p.hole_diameter)
            span_a = p.leg_a - start
            xs = [start + span_a * (i + 0.5) / p.holes_per_leg for i in range(p.holes_per_leg)]
            ys = [p.width / 4 if p.gusset else 0.0]
            for x in xs:
                for y in ys:
                    cyl = cq.Workplane("XY").center(x, y).circle(p.hole_diameter / 2).extrude(p.thickness)
                    body = body.cut(cyl)
                    if p.gusset:
                        body = body.cut(cyl.translate((0, -2 * y, 0)))
            span_b = p.leg_b - start
            zs = [start + span_b * (i + 0.5) / p.holes_per_leg for i in range(p.holes_per_leg)]
            for z in zs:
                for y in ys:
                    cyl = cq.Workplane("YZ").center(y, z).circle(p.hole_diameter / 2).extrude(p.thickness)
                    body = body.cut(cyl)
                    if p.gusset:
                        body = body.cut(cyl.translate((0, -2 * y, 0)))
        n_holes = 2 * p.holes_per_leg * (2 if p.gusset else 1) if p.hole_diameter > 0 else 0
        return TemplateResult(
            parts=[PartSolid("l_bracket", body, (p.leg_a, p.width, p.leg_b))],
            hardware=[HardwareItem(f"Bolt for {p.hole_diameter} mm hole", n_holes)] if n_holes else [],
            assumptions=[
                "Print with the outer corner on the bed; layer lines then run along both legs.",
                "Load capacity is not calculated; FDM parts are weakest between layers.",
            ],
        )


# --------------------------------------------------------------------------- Standoff


class StandoffParams(BaseModel):
    height: float = Field(10, ge=1, le=100)
    outer_diameter: float = Field(6, ge=3, le=40)
    hole_diameter: float = Field(3.2, ge=0, le=30)
    hexagonal: bool = False

    @model_validator(mode="after")
    def _check(self) -> StandoffParams:
        if self.outer_diameter - self.hole_diameter < 1.6:
            raise ValueError("wall thinner than 0.8 mm")
        return self


class Standoff(CadTemplate):
    key = "standoff"
    title = "Spacer / standoff"
    description = "Round or hexagonal spacer with a through hole."
    Params = StandoffParams

    def build(self, params: BaseModel) -> TemplateResult:
        p = StandoffParams.model_validate(params.model_dump())
        wp = cq.Workplane("XY")
        body = wp.polygon(6, p.outer_diameter).extrude(p.height) if p.hexagonal else wp.circle(p.outer_diameter / 2).extrude(p.height)
        if p.hole_diameter > 0:
            body = body.faces(">Z").workplane().hole(p.hole_diameter)
        bb = body.val().BoundingBox()
        return TemplateResult(parts=[PartSolid("standoff", body, (bb.xlen, bb.ylen, p.height))])


TEMPLATES: dict[str, CadTemplate] = {
    t.key: t for t in (ArduinoUnoHolder(), VentedEnclosure(), BatteryEnclosure(), LBracket(), Standoff())
}


def template_catalog() -> list[dict[str, Any]]:
    return [
        {"key": t.key, "title": t.title, "description": t.description, "params_schema": t.Params.model_json_schema()}
        for t in TEMPLATES.values()
    ]
