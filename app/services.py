from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import geometry
from .models import Calibration, Image, Measurement
from .schemas import GcpIn


def gcp_dicts(points: list[GcpIn]) -> list[dict[str, Any]]:
    out = []
    for i, p in enumerate(points):
        out.append(
            {
                "name": p.name or f"P{i + 1}",
                "x": p.x,
                "y": p.y,
                "X": p.X,
                "Y": p.Y,
                "sigma": p.sigma,
            }
        )
    return out


def run_fit(points: list[dict[str, Any]], default_sigma: float) -> dict[str, Any]:
    """薄封装：点里缺 sigma 时补缺省值，再调用几何拟合。"""
    norm = []
    for p in points:
        q = dict(p)
        if q.get("sigma") is None:
            q["sigma"] = default_sigma
        norm.append(q)
    return geometry.fit_homography(norm, default_sigma=default_sigma)


def evaluate_for(db: Session, image: Image, payload) -> tuple[Calibration | None, dict[str, Any]]:
    """测量换算的公共逻辑：选标定版本 -> 拟合（若为草稿）-> 误差传播。

    草稿（未保存标定版本）按当前点即时拟合；正式测量必须引用已保存版本。
    """
    cal: Calibration | None = None
    if payload.calibration_id is not None:
        cal = db.get(Calibration, payload.calibration_id)
        if cal is None or cal.image_id != image.id:
            raise geometry.GeometryError("标定版本不存在或不属于该图片")
        fit = {
            "h_vector": json.loads(cal.h_vector),
            "homography": json.loads(cal.homography),
            "covariance": json.loads(cal.covariance_json),
            "sigma0": None,
        }
    else:
        raise geometry.GeometryError("请先标定并保存一个版本，再进行测量")

    result = geometry.evaluate_measurement(
        fit,
        payload.points,
        kind=payload.kind,
        endpoint_sigma=payload.endpoint_sigma,
        include_calibration_uncertainty=payload.include_calibration_uncertainty,
    )
    return cal, result


def calibration_to_dict(cal: Calibration) -> dict[str, Any]:
    import re

    m = re.search(r"default_sigma=([0-9.eE+-]+)", cal.point_sigma_note or "")
    default_sigma = float(m.group(1)) if m else None
    return {
        "id": cal.id,
        "image_id": cal.image_id,
        "version": cal.version,
        "name": cal.name,
        "is_current": cal.is_current,
        "default_sigma": default_sigma,
        "points": json.loads(cal.points_json),
        "h_vector": json.loads(cal.h_vector),
        "homography": json.loads(cal.homography),
        "homography_world_from_image": json.loads(cal.homography_iw),
        "covariance": json.loads(cal.covariance_json),
        "residuals": json.loads(cal.residuals_json),
        "rms_image": cal.rms_image,
        "rms_world": cal.rms_world,
        "sigma0": cal.sigma0,
        "created_at": cal.created_at.isoformat(),
    }


def measurement_to_dict(m: Measurement, unit: str) -> dict[str, Any]:
    return {
        "id": m.id,
        "image_id": m.image_id,
        "calibration_id": m.calibration_id,
        "kind": m.kind,
        "label": m.label,
        "points": json.loads(m.points_json),
        "endpoint_sigma": m.endpoint_sigma,
        "length": m.length,
        "length_sigma": m.length_sigma,
        "area": m.area,
        "area_sigma": m.area_sigma,
        "perimeter": m.perimeter,
        "unit": unit,
        "created_at": m.created_at.isoformat(),
    }


def image_to_dict(image: Image, db: Session) -> dict[str, Any]:
    current = db.scalar(
        select(Calibration)
        .where(Calibration.image_id == image.id, Calibration.is_current.is_(True))
        .order_by(Calibration.version.desc())
    )
    return {
        "id": image.id,
        "name": image.name,
        "url": f"/images/{image.filename}",
        "width": image.width,
        "height": image.height,
        "unit": image.unit,
        "note": image.note,
        "current_calibration_id": current.id if current else None,
        "calibration_count": len(image.calibrations),
        "measurement_count": len(image.measurements),
        "created_at": image.created_at.isoformat(),
    }
