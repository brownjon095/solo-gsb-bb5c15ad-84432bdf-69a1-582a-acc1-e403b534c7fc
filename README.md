# 可复核图像测量（Photo Measurement）

在显微照片或工程照片上做**可复核（auditable）**平面测量的 Web 应用：

1. 上传图片，在图上标记 **≥ 4 个已知实际坐标** 的控制点（GCP）；
2. 后端用**加权最小二乘**拟合**含透视的平面变换（单应矩阵 Homography，8 参数）**，
   先做 Hartley 归一化 DLT 求初值，再做带阻尼回退的高斯-牛顿精化；
3. 显示**逐点残差**（图像像素 / 实际单位 / 归一化 σ 倍数）、RMS 与后验 σ₀，
   可反复调整点位后重新拟合；确认无误后**保存为带版本号的标定**（可随时回滚载入旧版本）；
4. 在图上绘制**线段/折线**与**多边形**，后端实时换算**实际长度、多边形面积与周长**，
   并按一阶误差传播给出 **1σ（以及 2σ）不确定度**；
5. 标定版本、测量结果连同原始像素坐标一起持久化到 SQLite，测量记录可复核、可删除。

技术栈：FastAPI + SQLAlchemy + NumPy + SQLite + 原生 JS Canvas，无前端构建步骤，Docker Compose 一键启动。

---

## 快速开始

```bash
docker compose up -d --build
```

然后浏览器访问：

```
http://localhost:8000
```

数据（SQLite 数据库与上传的原图）持久化在 Docker 卷 `photo-measure-data`（容器内 `/data`）中，
重建容器不丢失。

停止 / 备份：

```bash
docker compose down            # 停止，保留数据卷
docker compose down -v         # 停止并删除全部数据
docker run --rm -v <项目目录>_photo-measure-data:/data -v $PWD:/backup alpine \
    tar czf /backup/backup.tgz -C /data .   # 备份数据卷
```

## 配置（可选）

复制 `.env.example` 为 `.env` 并按需修改，或用环境变量传入：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `APP_PORT` | `8000` | 宿主机映射端口（容器内固定 8000） |
| `MAX_UPLOAD_MB` | `50` | 单张图片上传大小上限 |
| `DEFAULT_POINT_SIGMA` | `0.8` | 控制点像素量测缺省 1σ（像素） |
| `DEFAULT_ENDPOINT_SIGMA` | `0.8` | 测量顶点像素量测缺省 1σ（像素） |
| `DATA_DIR` | `/data` | 数据库与图片存储目录（一般无需改动） |
| `DATABASE_URL` | `sqlite:////data/measure.db` | 数据库连接串，可换成其它 SQLAlchemy 后端 |

## 本地开发（不用 Docker）

需要 Python 3.11+：

```bash
pip install -r requirements.txt
DATA_DIR=./data uvicorn app.main:app --reload --port 8000
```

---

## 使用说明

### 1. 上传
首页选择图片（png/jpg/webp/tif/bmp）、填写名称并选择实际单位（mm / µm / m / 自定义由单位字段决定）。
建议实际单位与控制点坐标使用同一单位（例如控制点 X、Y 以 mm 给出，则结果为 mm、mm²）。

### 2. 标记控制点并拟合
- 选择「① 标定点」模式，**单击**图像添加控制点（可拖动调整，右键删除）；
- 在右侧表格填入每个点的**实际坐标 X、Y**（同一平面坐标系）；
- `σ` 列为该点的**像素量测 1σ 精度**，留空则使用统一缺省值；
  难以辨认的点给更大权重值（注意：σ 越大权重越低，权重 ∝ 1/σ²）；
- 点「**加权拟合（≥4点）**」。控制点应尽量布满画幅、避免共线，以减小透视病态。

### 3. 复核残差并保存版本
拟合后面板显示：

- **图像残差 RMS**（像素）与**实际残差 RMS**（实际单位）；
- **后验 σ₀**（归一化）：≈1 表示残差与给定的点 σ 相符；显著大于 1（如 >3）
  说明存在点误识别/坐标录错或透视模型不适用，应先排查；
- 每点残差的**归一化倍数（nσ）**：>3σ 红色提示，可重点复核该点；
- 图上用橙色箭头显示「观测点 → 拟合预测点」。

满意后点「**保存为新版本**」，版本自增（v1、v2…），新版本自动置为「当前」，
旧版本全部保留，可随时载入或重新设为当前。**注意：仅试拟合不保存时不能测量。**

4 个点恰好定解（多余观测自由度为 0）时无法估计经验 σ₀，此时标定参数协方差按 0 处理，
测量不确定度只含端点量测分量；建议使用 5 个以上控制点。

### 4. 绘制与实时测量
- 「② 线段/折线」：单击逐个加顶点（≥2），双击（或按 Enter / 完成按钮）结束；
- 「③ 多边形」：单击加顶点（≥3），**双击、回车或点击第一个点**闭合；
- 右键 / 撤销点按钮删除最后一个顶点，Esc 取消整段草稿；
- 右侧「测量结果」页实时显示：
  - 长度 L（多边形为周长）± 1σ、2σ 与相对不确定度；
  - 多边形面积 A ± 1σ 与相对不确定度；
  - 每个顶点反算的实际坐标。
- 可调整「端点量测 1σ」，并选择不确定度是否计入标定误差（推荐计入）；
- 点「**保存测量**」命名后入库，「已保存」页可查看、点选高亮或删除。

鼠标：滚轮以光标为中心缩放（0.02×–40×），工具栏按钮缩放/适配/1:1，
「✋ 平移」模式或鼠标中键拖动平移。

---

## 数学方法

### 标定模型
单应矩阵 P 把实际平面坐标 q=(X,Y) 映射为像素 p=(u,v)：

```
[u]   [h0 h1 h2] [X]
[v] = [h3 h4 h5] [Y] ,  h8 = 1（8 个自由参数）
[1]   [h6 h7  1] [1]
```

- 观测为像素 (u,v)，逐点给定 σᵢ，最小化 Σᵢ ‖(pᵢ − p̂ᵢ)/σᵢ‖²；
- 初值：图像与实际坐标分别做 Hartley 相似归一化，加权 DLT（SVD 零空间），再去归一化；
- 精化：对 8 参数做高斯-牛顿，带步长回退（line search）保证残差单调下降；
  残差/雅可比解析给出；
- 法方程 N = JᵀWJ，参数协方差 Cov(h) = σ₀² N⁻¹，
  σ₀² = WRSS / (2n − 8)（dof = 2n − 8；dof = 0 时无经验协方差）；
- 残差同时在图像空间（像素）和反投影的实际空间报告。

### 测量与不确定度传播
像素点 r 经 P⁻¹ 得实际坐标 w。w 对参数 h 的雅可比由隐函数求导：

```
A = ∂p_pred/∂w (2×2),  B = ∂p_pred/∂h (2×8)
∂w/∂h = −A⁻¹B
```

每顶点实际坐标协方差：

```
Sᵢ = (∂wᵢ/∂h) Cov(h) (∂wᵢ/∂h)ᵀ + σ_endpoint² I
```

长度（折线段/周长）与多边形面积（鞋带公式）均为各 wᵢ 的标量函数，
解析求出梯度 g = ∂f/∂wᵢ 后，一阶传播：

```
σ_f² = Σᵢ gᵢᵀ Sᵢ gᵢ
```

注意标定分量经共同的 Cov(h) 在不同顶点间相关，端点分量各顶点独立——两者均已包含。
这里报告的是 1σ 高斯近似（2σ 约对应 95% 量级），适合一般工程复核；
对强透视、顶点很靠近图像边界或接近退化的标定，应同时查看 σ₀ 与逐点残差谨慎采用。

## HTTP API（摘要）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/images` | 图片列表 |
| POST | `/api/images` | 上传（multipart：file/name/unit） |
| GET/DELETE | `/api/images/{id}` | 详情 / 删除（级联） |
| POST | `/api/calibrations/fit` | 试拟合（不落库），返回 H、协方差、残差 |
| POST | `/api/calibrations` | 保存标定新版本 |
| GET | `/api/images/{id}/calibrations` | 版本列表 |
| GET/DELETE | `/api/calibrations/{id}` | 版本详情 / 删除 |
| POST | `/api/calibrations/{id}/current` | 设为当前版本 |
| POST | `/api/measurements/preview` | 实时换算（不保存） |
| POST | `/api/measurements` | 保存测量 |
| GET | `/api/images/{id}/measurements` | 测量列表 |
| DELETE | `/api/measurements/{id}` | 删除测量 |

完整交互式文档：服务启动后访问 `http://localhost:8000/docs`。

## 目录结构

```
app/
  main.py        FastAPI 入口、建表、静态资源挂载
  config.py      数据目录/上限/缺省 σ 等配置
  database.py    SQLAlchemy 引擎与会话（SQLite 外键开启）
  models.py      Image / Calibration / Measurement 表
  schemas.py     请求校验
  geometry.py    单应拟合、残差、协方差传播（纯 NumPy，可独立测试）
  services.py    拟合/测量编排与序列化
  routes.py      API 路由
  static/        前端（index.html / styles.css / app.js，原生 JS Canvas）
Dockerfile, docker-compose.yml, requirements.txt
```

## 已知边界

- 单应模型假设被测特征位于**同一平面**且镜头畸变可忽略；明显的镜头畸变应先做去畸变；
- 像素坐标基于原图分辨率存储，缩放/平移仅影响显示，不影响测量；
- 应用不做用户认证，适合内网/本地使用；暴露到公网前请自行加鉴权与 HTTPS。
