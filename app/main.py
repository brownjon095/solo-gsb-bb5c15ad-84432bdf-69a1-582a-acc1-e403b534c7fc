from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import UPLOAD_DIR
from .database import Base, engine
from .routes import router

Base.metadata.create_all(bind=engine)

app = FastAPI(title="可复核图像测量", version="1.0.0")
app.include_router(router)

app.mount("/images", StaticFiles(directory=str(UPLOAD_DIR)), name="images")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def index():
    return FileResponse("app/static/index.html")
