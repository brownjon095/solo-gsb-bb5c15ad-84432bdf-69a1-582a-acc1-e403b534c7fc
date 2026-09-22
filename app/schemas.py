"""API 请求体的 Pydantic 模型。"""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field


class CalibPoint(BaseModel):
    x: float = Field(description="像素 x")
    y: float = Field(description="像素 y")
    wx: float = Field(description="物理坐标 X")
    wy: float = Field(description="物理坐标 Y")
    weight: float = Field(default=1.0, gt=0, description="相对权重，越大越信任")


class CalibrationCreate(BaseModel):
    name: str = Field(default="v1", min_length=1, max_length=100)
    unit: str = Field(default="mm", min_length=1, max_length=20)
    pick_sigma_px: float = Field(default=1.0, ge=0.0, le=100.0,
                                 description="选点标准差（像素），计入不确定度")
    points: List[CalibPoint]


class EvaluateRequest(BaseModel):
    mtype: Literal["points", "line", "polygon"]
    points: List[List[float]] = Field(description="像素坐标 [[x,y],...]")


class MeasurementCreate(BaseModel):
    label: str = Field(default="", max_length=200)
    mtype: Literal["line", "polygon"]
    points: List[List[float]]
