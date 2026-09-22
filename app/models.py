from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Image(Base):
    __tablename__ = "images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False, default="mm")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    calibrations: Mapped[list[Calibration]] = relationship(
        back_populates="image", cascade="all, delete-orphan", passive_deletes=True
    )
    measurements: Mapped[list[Measurement]] = relationship(
        back_populates="image", cascade="all, delete-orphan", passive_deletes=True
    )


class Calibration(Base):
    """一个标定版本：>=4 个已知坐标点，加权最小二乘拟合平面透视变换。"""

    __tablename__ = "calibrations"
    __table_args__ = (UniqueConstraint("image_id", "version", name="uq_calibration_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    image_id: Mapped[int] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # 标定点 [{name, x, y, X, Y, sigma}] 像素坐标 (x,y) + 实际坐标 (X,Y)
    points_json: Mapped[str] = mapped_column(Text, nullable=False)
    # 行向量 h (8) 与 3x3 单应矩阵（实际平面 -> 图像像素）
    h_vector: Mapped[str] = mapped_column(Text, nullable=False)
    homography: Mapped[str] = mapped_column(Text, nullable=False)
    # 逆单应（图像像素 -> 实际平面），前端实时换算直接使用
    homography_iw: Mapped[str] = mapped_column(Text, nullable=False)
    # 逐点残差与整体统计
    residuals_json: Mapped[str] = mapped_column(Text, nullable=False)
    rms_image: Mapped[float] = mapped_column(Float, nullable=False)          # 像素
    rms_world: Mapped[float] = mapped_column(Float, nullable=False)          # 实际单位
    sigma0: Mapped[float] = mapped_column(Float, nullable=False)             # 归一化 sigma0
    # 8 参数协方差矩阵（已乘 sigma0^2，若 dof=0 则退化为零矩阵）
    covariance_json: Mapped[str] = mapped_column(Text, nullable=False)
    point_sigma_note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    image: Mapped[Image] = relationship(back_populates="calibrations")
    measurements: Mapped[list[Measurement]] = relationship(
        back_populates="calibration", passive_deletes=True
    )


class Measurement(Base):
    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    image_id: Mapped[int] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"), nullable=False, index=True
    )
    calibration_id: Mapped[int] = mapped_column(
        ForeignKey("calibrations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # line | polygon
    label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # [{x, y}] 原始像素坐标
    points_json: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint_sigma: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)

    length: Mapped[float | None] = mapped_column(Float, nullable=True)
    length_sigma: Mapped[float | None] = mapped_column(Float, nullable=True)
    area: Mapped[float | None] = mapped_column(Float, nullable=True)
    area_sigma: Mapped[float | None] = mapped_column(Float, nullable=True)
    perimeter: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    image: Mapped[Image] = relationship(back_populates="measurements")
    calibration: Mapped[Calibration | None] = relationship(back_populates="measurements")
