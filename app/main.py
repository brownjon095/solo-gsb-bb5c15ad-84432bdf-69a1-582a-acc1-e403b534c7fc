"""FastAPI 入口：图片上传、标定版本管理、实时换算与测量保存。"""
from __future__ import annotations

import json
import os
import uuid

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from . import db, homography
from .schemas import CalibrationCreate, EvaluateRequest, MeasurementCreate

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

app = FastAPI(title="图像标定与可复核测量", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def startup() -> None:
    db.init_db()


# ---------------- 页面 ----------------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ---------------- 图片 ----------------

@app.post("/api/images")
def upload_image(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"不支持的文件类型 {ext!r}，允许: {sorted(ALLOWED_EXT)}")
    stored = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(db.UPLOAD_DIR, stored)
    with open(path, "wb") as f:
        f.write(file.file.read())
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            width, height = im.size
    except Exception:
        os.remove(path)
        raise HTTPException(400, "文件不是有效图片")
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO images (filename, stored_path, width, height) VALUES (?,?,?,?)",
            (file.filename or stored, stored, width, height),
        )
        image_id = cur.lastrowid
    return {"id": image_id, "filename": file.filename, "width": width, "height": height}


@app.get("/api/images")
def list_images():
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, filename, width, height, uploaded_at FROM images ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/images/{image_id}")
def get_image(image_id: int):
    row = _get_image_or_404(image_id)
    return {"id": row["id"], "filename": row["filename"],
            "width": row["width"], "height": row["height"],
            "uploaded_at": row["uploaded_at"]}


@app.get("/api/images/{image_id}/file", include_in_schema=False)
def image_file(image_id: int):
    row = _get_image_or_404(image_id)
    return FileResponse(os.path.join(db.UPLOAD_DIR, row["stored_path"]))


@app.delete("/api/images/{image_id}")
def delete_image(image_id: int):
    row = _get_image_or_404(image_id)
    with db.get_conn() as conn:
        conn.execute("DELETE FROM images WHERE id=?", (image_id,))
    path = os.path.join(db.UPLOAD_DIR, row["stored_path"])
    if os.path.exists(path):
        os.remove(path)
    return {"ok": True}


# ---------------- 标定 ----------------

@app.post("/api/images/{image_id}/calibrations")
def create_calibration(image_id: int, body: CalibrationCreate):
    _get_image_or_404(image_id)
    px = [[p.x, p.y] for p in body.points]
    world = [[p.wx, p.wy] for p in body.points]
    weights = [p.weight for p in body.points]
    try:
        fit = homography.fit_homography(px, world, weights)
    except ValueError as e:
        raise HTTPException(422, str(e))
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO calibrations
               (image_id, name, unit, pick_sigma_px, points_json, homography_json,
                covariance_json, residuals_json, rms, max_res, sigma2, dof)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (image_id, body.name, body.unit, body.pick_sigma_px,
             json.dumps([p.model_dump() for p in body.points]), json.dumps(fit["H"]),
             json.dumps(fit["cov_h"]), json.dumps(fit["residuals"]),
             fit["rms"], fit["max_res"], fit["sigma2"], fit["dof"]),
        )
        calib_id = cur.lastrowid
    return {"id": calib_id, **{k: fit[k] for k in ("rms", "max_res", "sigma2", "dof", "residuals")}}


@app.get("/api/images/{image_id}/calibrations")
def list_calibrations(image_id: int):
    _get_image_or_404(image_id)
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT id, name, unit, pick_sigma_px, rms, max_res, dof, created_at,
                      json_array_length(points_json) AS n_points
               FROM calibrations WHERE image_id=? ORDER BY id DESC""",
            (image_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/calibrations/{calib_id}")
def get_calibration(calib_id: int):
    row = _get_calib_or_404(calib_id)
    return {
        "id": row["id"], "image_id": row["image_id"], "name": row["name"],
        "unit": row["unit"], "pick_sigma_px": row["pick_sigma_px"],
        "rms": row["rms"], "max_res": row["max_res"], "sigma2": row["sigma2"],
        "dof": row["dof"], "created_at": row["created_at"],
        "points": json.loads(row["points_json"]),
        "homography": json.loads(row["homography_json"]),
        "residuals": json.loads(row["residuals_json"]),
    }


@app.delete("/api/calibrations/{calib_id}")
def delete_calibration(calib_id: int):
    _get_calib_or_404(calib_id)
    with db.get_conn() as conn:
        conn.execute("DELETE FROM calibrations WHERE id=?", (calib_id,))
    return {"ok": True}


# ---------------- 换算与测量 ----------------

@app.post("/api/calibrations/{calib_id}/evaluate")
def evaluate(calib_id: int, body: EvaluateRequest):
    """实时换算：不保存，仅返回物理坐标/长度/面积及 1-sigma 不确定度。"""
    row = _get_calib_or_404(calib_id)
    try:
        result = homography.evaluate(
            json.loads(row["homography_json"]),
            json.loads(row["covariance_json"]),
            body.mtype,
            body.points,
            row["pick_sigma_px"],
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    result["unit"] = row["unit"]
    return result


@app.post("/api/calibrations/{calib_id}/measurements")
def create_measurement(calib_id: int, body: MeasurementCreate):
    """保存测量：服务端重新计算数值与不确定度，保证可复核。"""
    row = _get_calib_or_404(calib_id)
    try:
        result = homography.evaluate(
            json.loads(row["homography_json"]),
            json.loads(row["covariance_json"]),
            body.mtype,
            body.points,
            row["pick_sigma_px"],
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO measurements
               (image_id, calibration_id, label, mtype, points_json, value, uncertainty, unit)
               VALUES (?,?,?,?,?,?,?,?)""",
            (row["image_id"], calib_id, body.label, body.mtype,
             json.dumps(body.points), result["value"], result["uncertainty"], row["unit"]),
        )
        mid = cur.lastrowid
    return {"id": mid, "value": result["value"], "uncertainty": result["uncertainty"],
            "unit": row["unit"]}


@app.get("/api/images/{image_id}/measurements")
def list_measurements(image_id: int):
    _get_image_or_404(image_id)
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT m.id, m.label, m.mtype, m.points_json, m.value, m.uncertainty,
                      m.unit, m.created_at, c.name AS calibration_name, m.calibration_id
               FROM measurements m JOIN calibrations c ON c.id = m.calibration_id
               WHERE m.image_id=? ORDER BY m.id DESC""",
            (image_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["points"] = json.loads(d.pop("points_json"))
        out.append(d)
    return out


@app.delete("/api/measurements/{measurement_id}")
def delete_measurement(measurement_id: int):
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM measurements WHERE id=?", (measurement_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "测量记录不存在")
    return {"ok": True}


# ---------------- 工具 ----------------

def _get_image_or_404(image_id: int):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "图片不存在")
    return row


def _get_calib_or_404(calib_id: int):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM calibrations WHERE id=?", (calib_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "标定不存在")
    return row
