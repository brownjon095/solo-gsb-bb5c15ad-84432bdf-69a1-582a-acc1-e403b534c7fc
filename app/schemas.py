from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class GcpIn(BaseModel):
    name: str = ""
    x: float
    y: float
    X: float
    Y: float
    sigma: float | None = Field(default=None, gt=0)

    @field_validator("x", "y", "X", "Y")
    @classmethod
    def finite(cls, v: float) -> float:
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("坐标必须为有限数值")
        return v


class CalibrationFitIn(BaseModel):
    image_id: int
    unit: str = "mm"
    points: list[GcpIn] = Field(min_length=4)
    default_sigma: float = Field(default=0.8, gt=0)


class CalibrationSaveIn(BaseModel):
    image_id: int
    name: str = ""
    points: list[GcpIn] = Field(min_length=4)
    default_sigma: float = Field(default=0.8, gt=0)
    make_current: bool = True


class MeasurementIn(BaseModel):
    image_id: int
    calibration_id: int | None = None
    kind: Literal["line", "polygon"]
    label: str = ""
    points: list[dict] = Field(min_length=2)
    endpoint_sigma: float = Field(default=0.8, gt=0)
    include_calibration_uncertainty: bool = True

    @field_validator("points")
    @classmethod
    def valid_points(cls, v: list[dict]) -> list[dict]:
        for p in v:
            if "x" not in p or "y" not in p:
                raise ValueError("每个顶点必须含 x, y")
            float(p["x"])
            float(p["y"])
        return v

    @model_validator(mode="after")
    def polygon_min_three(self):
        if self.kind == "polygon" and len(self.points) < 3:
            raise ValueError("多边形至少需要 3 个顶点")
        return self
