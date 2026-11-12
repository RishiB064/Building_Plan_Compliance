"""Data contract for POST /v1/runs (F1, F2, D1).

Schema, type, range, category and cross-field validation all live here and run at the
ingestion boundary, before any agent starts. Rejects are quarantined with field-level errors.
"""
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

CONTRACT_VERSION = "2.0.0"


class BuildingUse(str, Enum):
    residential = "residential"
    commercial = "commercial"
    mixed_use = "mixed-use"
    institutional = "institutional"


class Zone(str, Enum):
    residential = "residential"
    commercial = "commercial"
    mixed = "mixed"
    industrial = "industrial"
    public_semipublic = "public-semipublic"


class ProposalIn(BaseModel):
    """A building proposal: structured parameters plus free-text notes."""

    model_config = ConfigDict(extra="forbid")

    plot_area_sqm: float = Field(gt=10, le=100_000, description="Plot area in square metres")
    road_width_m: float = Field(gt=0, le=60, description="Width of the abutting road in metres")
    building_height_m: float = Field(gt=2, le=150, description="Proposed height in metres")
    num_floors: int = Field(ge=1, le=60, description="Number of floors including ground")
    built_up_area_sqm: float = Field(gt=0, le=1_000_000, description="Total built-up area, all floors")
    ground_coverage_pct: float = Field(ge=0, le=100, description="Ground coverage as % of plot")
    parking_spaces: int = Field(ge=0, le=5000, description="Car parking spaces provided")
    stilt_height_m: float = Field(default=0, ge=0, le=6, description="Height of an open parking (stilt) floor at the bottom, 0 if none. Counted inside building_height_m")
    plot_depth_m: float | None = Field(default=None, gt=0, le=1000, description="Plot depth, front to back (needed for plots above 250 sq m)")
    plot_width_m: float | None = Field(default=None, gt=0, le=1000, description="Plot width, side to side (needed for plots above 250 sq m)")
    front_setback_m: float = Field(ge=0, le=100, description="Gap you leave between the front plot edge and the building")
    rear_setback_m: float = Field(ge=0, le=100, description="Gap at the back")
    left_setback_m: float = Field(ge=0, le=100, description="Gap on the left side")
    right_setback_m: float = Field(ge=0, le=100, description="Gap on the right side")
    building_use: BuildingUse
    zone: Zone
    notes: str = Field(default="", max_length=2000, description="Free-text notes (untrusted input)")

    @model_validator(mode="after")
    def cross_field(self):
        errs = []
        per_floor = self.building_height_m / self.num_floors
        if not (2.4 <= per_floor <= 6.0):
            errs.append(
                f"building_height_m/num_floors = {per_floor:.2f} m per floor, outside plausible 2.4-6.0 m"
            )
        if self.built_up_area_sqm > self.plot_area_sqm * self.num_floors * 1.0001:
            errs.append("built_up_area_sqm cannot exceed plot_area_sqm * num_floors")
        ground_area = self.plot_area_sqm * self.ground_coverage_pct / 100
        if ground_area > 0 and self.built_up_area_sqm < ground_area * 0.999:
            errs.append("built_up_area_sqm is smaller than the implied ground-floor area")
        if self.num_floors == 1 and self.ground_coverage_pct > 0 and \
                abs(self.built_up_area_sqm - ground_area) > 0.05 * max(ground_area, 1):
            errs.append("single-floor building: built_up_area_sqm should match plot_area * ground_coverage_pct")
        if self.stilt_height_m > 0 and self.stilt_height_m > self.building_height_m - 2.4:
            errs.append("stilt_height_m leaves no room for any floor above the parking floor")
        if self.plot_depth_m and self.plot_width_m:
            area = self.plot_depth_m * self.plot_width_m
            if abs(area - self.plot_area_sqm) > 0.25 * self.plot_area_sqm:
                errs.append("plot_depth_m x plot_width_m should be within 25% of plot_area_sqm")
            if self.front_setback_m + self.rear_setback_m >= self.plot_depth_m:
                errs.append("front and rear setbacks together are as deep as the whole plot")
            if self.left_setback_m + self.right_setback_m >= self.plot_width_m:
                errs.append("left and right setbacks together are as wide as the whole plot")
        if errs:
            raise ValueError("; ".join(errs))
        return self


def validate_payload(payload) -> tuple[ProposalIn | None, list[dict]]:
    """Return (model, []) on success or (None, field_errors) on failure."""
    if not isinstance(payload, dict):
        return None, [{"field": "$", "type": "type_error", "message": "body must be a JSON object"}]
    try:
        return ProposalIn.model_validate(payload), []
    except ValidationError as e:
        out = []
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "$"
            out.append({"field": loc, "type": err["type"], "message": err["msg"]})
        return None, out


def contract_document() -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "request_schema": ProposalIn.model_json_schema(),
        "cross_field_rules": [
            "2.4 <= building_height_m / num_floors <= 6.0",
            "built_up_area_sqm <= plot_area_sqm * num_floors",
            "built_up_area_sqm >= plot_area_sqm * ground_coverage_pct / 100",
            "single-floor: built_up_area_sqm ~= plot_area_sqm * ground_coverage_pct / 100 (5% tolerance)",
            "stilt_height_m <= building_height_m - 2.4",
            "if plot_depth_m and plot_width_m are given: their product within 25% of plot_area_sqm; front+rear setback < depth; left+right setback < width",
        ],
        "response_schema": {
            "route": ["clear-for-submission", "revise-and-recheck", "specialist-review"],
            "findings[]": {"clause_id": "str", "status": "satisfied|violated|needs_review|info",
                           "reason": "str", "evidence_quote": "str (verbatim from clause)"},
            "disclaimer": "Findings are evidence for a licensed architect/engineer. This service never certifies compliance.",
        },
    }
