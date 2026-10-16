import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .environment import discover_orfs

NAME = r"[A-Za-z_][A-Za-z0-9_]*"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Clock(Strict):
    port: str
    period_ns: float = Field(gt=0)

    @field_validator("port")
    @classmethod
    def port_name(cls, value):
        if not re.fullmatch(NAME, value):
            raise ValueError("clock port must be a simple HDL identifier")
        return value


class Simulation(Strict):
    top: str
    testbench: list[str] = Field(min_length=1)
    pass_marker: str = "PASS"
    timeout_seconds: int = Field(default=60, ge=1, le=86400)
    cell_models: list[str] = []

    @field_validator("top")
    @classmethod
    def top_name(cls, value):
        if not re.fullmatch(NAME, value):
            raise ValueError("invalid simulation top")
        return value

    @field_validator("pass_marker")
    @classmethod
    def marker(cls, value):
        if not value.strip():
            raise ValueError("a nonempty explicit test pass marker is required")
        return value


class Project(Strict):
    name: str
    top: str
    rtl: list[str] = Field(min_length=1)
    orfs: str = Field(default_factory=discover_orfs)
    platform: str = "nangate45"
    clocks: list[Clock] = []
    sdc: str | None = None
    include_dirs: list[str] = []
    simulation: Simulation | None = None
    physical: dict[str, str | float | int] = {}
    save_images: bool = False
    threads: int = Field(default=2, ge=1, le=256)
    timeout_seconds: int = Field(default=3600, ge=1, le=604800)

    @field_validator("name", "top", "platform")
    @classmethod
    def names(cls, value):
        if not re.fullmatch(NAME, value):
            raise ValueError(
                "use letters, digits and underscores; start with a letter or underscore"
            )
        return value

    @field_validator("physical")
    @classmethod
    def variables(cls, values):
        allowed = {
            "CORE_UTILIZATION",
            "CORE_ASPECT_RATIO",
            "CORE_MARGIN",
            "DIE_AREA",
            "CORE_AREA",
            "PLACE_DENSITY",
            "PLACE_DENSITY_LB_ADDON",
            "TNS_END_PERCENT",
            "ABC_AREA",
            "ADDER_MAP_FILE",
            "SYNTH_HIERARCHICAL",
            "OPENROAD_HIERARCHICAL",
            "SYNTH_REPEATABLE_BUILD",
            "MIN_ROUTING_LAYER",
            "MAX_ROUTING_LAYER",
            "IO_PLACER_H",
            "IO_PLACER_V",
        }
        for key, value in values.items():
            if key not in allowed or not re.fullmatch(r"[A-Za-z0-9_. +\-]*", str(value)):
                raise ValueError(f"unsupported or unsafe physical setting: {key}")
        return values

    @model_validator(mode="after")
    def timing(self):
        if not self.sdc and not self.clocks:
            raise ValueError("provide clocks or an SDC file")
        if len({c.port for c in self.clocks}) != len(self.clocks):
            raise ValueError("duplicate clock port")
        return self


def safe_make_path(path: Path) -> str:
    value = str(path.resolve())
    if not re.fullmatch(r"[A-Za-z0-9_./+\-]+", value):
        raise ValueError(f"ORFS paths must avoid whitespace and Make/shell metacharacters: {path}")
    return value


def load_project(path: Path) -> tuple[Project, Path]:
    path = path.expanduser().resolve()
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(
            "The project YAML could not be read. Check its indentation and syntax."
        ) from exc
    return Project.model_validate(data), path.parent
