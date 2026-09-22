from __future__ import annotations

import io
import json
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image as PILImage
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import services
from .config import DEFAULT_ENDPOINT_SIGMA, MAX_UPLOAD_BYTES, UPLOAD_DIR
from .database import get_db
from .geometry import GeometryError
from .models import Calibration, Image, Measurement
from .schemas import CalibrationFitIn, CalibrationSaveIn, MeasurementIn

router = APIRouter(prefix="/api")


def _get_image(db: Session, image_id: int) -> Image:
    image = db.get(Image, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    return image


# ---------------------------------------------------------------------------
# 图片
# ---------------------------------------------------------------------------

@router.get("/images")
def list_images(db: Session = Depends(get_db)):
    rows = db.scalars(select(Image).order_by(Image.created_at.desc())).all()
    return [services.image_to_dict(im, db) for im in rows]


@router.post("/images", status_code=201)
async def upload_image(
    file: UploadFile = File(...),
    name: str = Form(""),
    unit: str = Form("mm"),
    db: Session = Depends(get_db),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="图片超过大小限制")

    suffix = (file.filename or "").rsplit(".", 1)[-1].lower()
    if suffix not in {"png", "jpg", "jpeg", "webp", "tif", "tiff", "bmp", "gif"}:
        raise HTTPException(status_code=400, detail="不支持的图片格式")

    try:
        with PILImage.open(io.BytesIO(data)) as im:
            im.verify()
        with PILImage.open(io.BytesIO(data)) as im:
            width, height = im.size
            mode = im.mode
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="无法解析的图片文件")

    filename = f"{uuid.uuid4().hex}.{suffix}"
    (UPLOAD_DIR / filename).write_bytes(data)

    image = Image(
        name=name or file.filename or filename,
        filename=filename,
        width=width,
        height=height,
        unit=unit.strip() or "mm",
        note=mode,
    )
    db.add(image)
    db.commit()
    db.refresh(image)
    return services.image_to_dict(image, db)


@router.get("/images/{image_id}")
def get_image(image_id: int, db: Session = Depends(get_db)):
    return services.image_to_dict(_get_image(db, image_id), db)


@router.delete("/images/{image_id}", status_code=204)
def delete_image(image_id: int, db: Session = Depends(get_db)):
    image = _get_image(db, image_id)
    try:
        (UPLOAD_DIR / image.filename).unlink(missing_ok=True)
    except OSError:
        pass
    db.delete(image)
    db.commit()


# ---------------------------------------------------------------------------
# 标定
# ---------------------------------------------------------------------------

def _fit_payload(payload, db: Session | None = None) -> dict:
    try:
        fit = services.run_fit(services.gcp_dicts(payload.points), payload.default_sigma)
    except GeometryError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return fit


@router.post("/calibrations/fit")
def fit_calibration_preview(payload: CalibrationFitIn, db: Session = Depends(get_db)):
    """不落库的试拟合：返回单应、协方差与逐点残差供复核。"""
    fit = _fit_payload(payload)
    return {
        "points": payload.model_dump()["points"],
        "h_vector": fit["h_vector"],
        "homography": fit["homography"],
        "homography_world_from_image": fit["homography_world_from_image"],
        "covariance": fit["covariance"],
        "sigma0": fit["sigma0"],
        "dof": fit["dof"],
        "rms_image": fit["rms_image"],
        "rms_world": fit["rms_world"],
        "residuals": fit["point_residuals"],
        "unit": _get_image(db, payload.image_id).unit if payload.image_id else payload.unit,
    }


@router.post("/calibrations", status_code=201)
def save_calibration(payload: CalibrationSaveIn, db: Session = Depends(get_db)):
    image = _get_image(db, payload.image_id)
    fit = _fit_payload(payload)

    next_version = db.scalar(
        select(func.coalesce(func.max(Calibration.version), 0)).where(
            Calibration.image_id == image.id
        )
    ) + 1

    if payload.make_current:
        for c in db.scalars(select(Calibration).where(Calibration.image_id == image.id)).all():
            c.is_current = False

    cal = Calibration(
        image_id=image.id,
        version=next_version,
        name=payload.name.strip(),
        is_current=payload.make_current,
        points_json=json.dumps(services.gcp_dicts(payload.points), ensure_ascii=False),
        h_vector=json.dumps(fit["h_vector"]),
        homography=json.dumps(fit["homography"]),
        homography_iw=json.dumps(fit["homography_world_from_image"]),
        residuals_json=json.dumps(fit["point_residuals"], ensure_ascii=False),
        rms_image=fit["rms_image"],
        rms_world=fit["rms_world"],
        sigma0=fit["sigma0"] if fit["sigma0"] is not None else 0.0,
        covariance_json=json.dumps(fit["covariance"]),
        point_sigma_note=f"default_sigma={payload.default_sigma}",
    )
    db.add(cal)
    db.commit()
    db.refresh(cal)
    return services.calibration_to_dict(cal)


@router.get("/images/{image_id}/calibrations")
def list_calibrations(image_id: int, db: Session = Depends(get_db)):
    _get_image(db, image_id)
    rows = db.scalars(
        select(Calibration)
        .where(Calibration.image_id == image_id)
        .order_by(Calibration.version.desc())
    ).all()
    return [
        {
            "id": c.id,
            "version": c.version,
            "name": c.name,
            "is_current": c.is_current,
            "rms_image": c.rms_image,
            "rms_world": c.rms_world,
            "sigma0": c.sigma0,
            "created_at": c.created_at.isoformat(),
        }
        for c in rows
    ]


@router.get("/calibrations/{calibration_id}")
def get_calibration(calibration_id: int, db: Session = Depends(get_db)):
    cal = db.get(Calibration, calibration_id)
    if cal is None:
        raise HTTPException(status_code=404, detail="标定版本不存在")
    out = services.calibration_to_dict(cal)
    out["unit"] = cal.image.unit
    return out


@router.post("/calibrations/{calibration_id}/current")
def set_current_calibration(calibration_id: int, db: Session = Depends(get_db)):
    cal = db.get(Calibration, calibration_id)
    if cal is None:
        raise HTTPException(status_code=404, detail="标定版本不存在")
    for c in db.scalars(select(Calibration).where(Calibration.image_id == cal.image_id)).all():
        c.is_current = c.id == cal.id
    db.commit()
    return {"id": cal.id, "is_current": True}


@router.delete("/calibrations/{calibration_id}", status_code=204)
def delete_calibration(calibration_id: int, db: Session = Depends(get_db)):
    cal = db.get(Calibration, calibration_id)
    if cal is None:
        raise HTTPException(status_code=404, detail="标定版本不存在")
    db.delete(cal)
    db.commit()


# ---------------------------------------------------------------------------
# 测量
# ---------------------------------------------------------------------------

@router.post("/measurements/preview")
def preview_measurement(payload: MeasurementIn, db: Session = Depends(get_db)):
    image = _get_image(db, payload.image_id)
    try:
        _, result = services.evaluate_for(db, image, payload)
    except GeometryError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    result["unit"] = image.unit
    return result


@router.post("/measurements", status_code=201)
def create_measurement(payload: MeasurementIn, db: Session = Depends(get_db)):
    image = _get_image(db, payload.image_id)
    try:
        cal, result = services.evaluate_for(db, image, payload)
    except GeometryError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    m = Measurement(
        image_id=image.id,
        calibration_id=cal.id if cal else None,
        kind=payload.kind,
        label=payload.label.strip(),
        points_json=json.dumps(
            [{"x": float(p["x"]), "y": float(p["y"])} for p in payload.points]
        ),
        endpoint_sigma=payload.endpoint_sigma or DEFAULT_ENDPOINT_SIGMA,
        length=result["length"],
        length_sigma=result["length_sigma"],
        area=result["area"],
        area_sigma=result["area_sigma"],
        perimeter=result["perimeter"],
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return services.measurement_to_dict(m, image.unit)


@router.get("/images/{image_id}/measurements")
def list_measurements(image_id: int, db: Session = Depends(get_db)):
    image = _get_image(db, image_id)
    rows = db.scalars(
        select(Measurement)
        .where(Measurement.image_id == image_id)
        .order_by(Measurement.created_at.desc())
    ).all()
    out = []
    for m in rows:
        d = services.measurement_to_dict(m, image.unit)
        d["calibration_version"] = (
            db.get(Calibration, m.calibration_id).version if m.calibration_id else None
        )
        out.append(d)
    return out


@router.delete("/measurements/{measurement_id}", status_code=204)
def delete_measurement(measurement_id: int, db: Session = Depends(get_db)):
    m = db.get(Measurement, measurement_id)
    if m is None:
        raise HTTPException(status_code=404, detail="测量不存在")
    db.delete(m)
    db.commit()
