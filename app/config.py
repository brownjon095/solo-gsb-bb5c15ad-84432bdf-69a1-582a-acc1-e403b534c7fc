from __future__ import annotations

import os
from pathlib import Path

# 持久化数据目录（SQLite 数据库 + 上传图片），可用环境变量覆盖
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = os.getenv("DB_PATH", str(DATA_DIR / "measure.db"))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# 标定点坐标量测不确定度缺省值（像素，1σ）
DEFAULT_POINT_SIGMA = float(os.getenv("DEFAULT_POINT_SIGMA", "0.8"))
# 绘制测量端点量测不确定度缺省值（像素，1σ）
DEFAULT_ENDPOINT_SIGMA = float(os.getenv("DEFAULT_ENDPOINT_SIGMA", "0.8"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
