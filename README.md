# 图像标定与可复核测量平台

在显微照片或工程照片上做**可复核**的尺寸测量：上传图片 → 标记 ≥4 个已知坐标点 →
加权最小二乘拟合含透视的平面变换（单应矩阵）并展示逐点残差 → 绘制线段 / 多边形，
实时换算实际长度、面积及 1σ 不确定度。标定版本与测量结果均存入 SQLite，可随时回溯复查。

## 快速开始（Docker Compose 一键启动）

```bash
docker compose up --build
```

启动后访问 **http://localhost:8000**

- 交互式 API 文档：http://localhost:8000/docs
- 数据（SQLite 库 `app.db` + 上传图片）持久化在宿主机 `./data/` 目录。

### 配置

| 配置项 | 位置 | 默认值 | 说明 |
|---|---|---|---|
| 对外端口 | `docker-compose.yml` 的 `ports` | `8000:8000` | 改左侧数字换端口，如 `"9000:8000"` |
| 数据目录 | 环境变量 `DATA_DIR` | 容器内 `/data` | SQLite 与上传图片存放位置 |
| 数据卷 | `docker-compose.yml` 的 `volumes` | `./data:/data` | 宿主机持久化路径 |

## 本地开发（不用 Docker）

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

运行数学核心自检（拟合精度、不确定度、加权抗粗差、4 点定解回退）：

```bash
python tests/test_homography.py
```

## 使用流程

1. **上传图片**：左侧面板选择文件（png/jpg/tif/bmp/webp）。
2. **标定**：在画布上点击 ≥4 个已知物理坐标的点（如标尺刻度、标定板角点），
   在表格中填入每个点的物理 X/Y 与权重（权重 ∝ 1/σ²，可疑点给小权重），
   设置版本名、单位（mm/µm/in…）和选点 σ（像素点击噪声，默认 1px），
   点击「拟合并保存标定」。随后显示 RMS 残差与逐点残差表（物理单位 + 像素重投影误差）。
   同一图片可保存多个标定版本，下拉切换、可删除。
3. **测量**：选「线段」（点 2 个端点）或「多边形」（逐点点击，双击或点回起点闭合）。
   绘制过程中实时显示长度/面积 ± 不确定度；填备注后「保存测量」。
   服务端在保存时**重新独立计算**数值与不确定度，保证结果可复核。
   鼠标悬停测量列表可在画布上高亮对应图形。

## 测量模型与不确定度说明

- **变换模型**：8 自由度单应（透视）变换，像素 (x,y) → 物理 (X,Y)，
  h33 固定为 1，每点 2 个线性方程，加权最小二乘求解（≥4 点）。
- **参数协方差**：`cov(h) = σ² · (AᵀWA)⁻¹`，其中 `σ² = χ²/(2N−8)`；
  恰好 4 点（零残差）时回退为 `σ² = mean(1/wᵢ)` 的保守估计。
- **不确定度传播**：测量点物理坐标的协方差由雅可比传播得到，
  包含标定参数不确定性（同一变换下各点误差**相关**，整体传播而非逐点独立）
  与独立的选点噪声（选点 σ）。长度/面积对其顶点坐标求梯度后，
  经联合协方差传播得到 1σ 标准不确定度。
- **残差表**：逐点给出物理坐标残差 (dX, dY)、|残差| 及像素重投影误差，
  用于发现粗差点（可降权重后重新拟合）。

## API 概览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/images` | 上传图片（multipart） |
| GET | `/api/images` / `/api/images/{id}` | 图片列表 / 元信息 |
| GET | `/api/images/{id}/file` | 图片文件 |
| DELETE | `/api/images/{id}` | 删除图片（级联删除标定与测量） |
| POST | `/api/images/{id}/calibrations` | 拟合并保存标定版本 |
| GET | `/api/images/{id}/calibrations` | 标定版本列表 |
| GET / DELETE | `/api/calibrations/{id}` | 标定详情（含残差）/ 删除 |
| POST | `/api/calibrations/{id}/evaluate` | 实时换算（points/line/polygon，不落库） |
| POST | `/api/calibrations/{id}/measurements` | 保存测量（服务端重算） |
| GET | `/api/images/{id}/measurements` | 测量列表 |
| DELETE | `/api/measurements/{id}` | 删除测量 |

## 项目结构

```
app/
  main.py          FastAPI 路由
  homography.py    加权单应拟合 + 不确定度传播（numpy）
  db.py            SQLite 数据访问
  schemas.py       请求体校验
  static/          单页前端（原生 JS + Canvas）
tests/test_homography.py   数学核心自检
Dockerfile / docker-compose.yml / requirements.txt
```
