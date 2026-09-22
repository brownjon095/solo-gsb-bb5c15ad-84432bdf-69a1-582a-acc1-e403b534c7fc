"""加权最小二乘单应（透视平面变换）拟合与不确定度传播。

模型：像素坐标 p=(x,y) -> 物理坐标 P=(X,Y)，使用 8 自由度单应矩阵 H（h33 固定为 1）：

    X = (h11 x + h12 y + h13) / (h31 x + h32 y + 1)
    Y = (h21 x + h22 y + h23) / (h31 x + h32 y + 1)

拟合：每个标定点贡献 2 行线性方程，按权重 w_i 加权（行乘以 sqrt(w_i)），
用最小二乘求解 8 个未知数（至少 4 个点）。

不确定度：
  - 标定参数协方差 cov(h) = sigma2 * (A^T W A)^-1，
    sigma2 = chi2 / dof（dof = 2N-8；N=4 时回退为 mean(1/w_i)，即把权重
    解释为 1/物理单位^2 的保守估计）。
  - 任意像素点变换后的物理坐标协方差由雅可比传播得到，并叠加
    “选点误差”（pick_sigma_px，像素坐标点击噪声，各点独立）。
  - 长度/面积对标定参数的相关性通过整体雅可比正确处理（同一 H 作用于
    所有顶点，误差相关，不能简单按独立点处理）。
"""
from __future__ import annotations

import math

import numpy as np

MIN_CALIB_POINTS = 4


def fit_homography(px, world, weights) -> dict:
    """加权最小二乘拟合单应矩阵。

    px:      (N,2) 像素坐标
    world:   (N,2) 已知物理坐标
    weights: (N,)  相对权重（越大越信任）

    返回 dict：H(3x3)、cov_h(8x8)、sigma2、dof、rms、max_res、residuals（逐点明细）
    """
    px = np.asarray(px, dtype=float)
    world = np.asarray(world, dtype=float)
    weights = np.asarray(weights, dtype=float)
    n = len(px)
    if n < MIN_CALIB_POINTS:
        raise ValueError(f"至少需要 {MIN_CALIB_POINTS} 个标定点，当前 {n} 个")
    if np.any(~np.isfinite(px)) or np.any(~np.isfinite(world)):
        raise ValueError("坐标中存在非法数值")
    if np.any(weights <= 0):
        raise ValueError("权重必须为正数")

    A = np.zeros((2 * n, 8))
    b = np.zeros(2 * n)
    for i in range(n):
        x, y = px[i]
        X, Y = world[i]
        A[2 * i] = [x, y, 1.0, 0.0, 0.0, 0.0, -x * X, -y * X]
        A[2 * i + 1] = [0.0, 0.0, 0.0, x, y, 1.0, -x * Y, -y * Y]
        b[2 * i] = X
        b[2 * i + 1] = Y

    sw = np.sqrt(np.repeat(weights, 2))  # 每个点贡献 2 行方程
    Aw = A * sw[:, None]
    bw = b * sw
    h, *_ = np.linalg.lstsq(Aw, bw, rcond=None)
    H = np.array([[h[0], h[1], h[2]],
                  [h[3], h[4], h[5]],
                  [h[6], h[7], 1.0]])

    pred = apply_homography(H, px)
    res = pred - world  # 物理单位残差
    chi2 = float(np.sum(weights * np.einsum("ij,ij->i", res, res)))
    dof = 2 * n - 8
    if dof > 0:
        sigma2 = chi2 / dof
    else:
        # 4 点恰好定解、无残差可估计：按权重=1/sigma^2 的绝对解释取典型方差
        sigma2 = float(np.mean(1.0 / weights))
    cov_h = sigma2 * np.linalg.inv(Aw.T @ Aw)

    # 像素重投影误差：把给定物理点经 H^{-1} 映回像素，与点击位置比较
    Hinv = np.linalg.inv(H)
    reproj = apply_homography(Hinv, world)
    reproj_err = np.linalg.norm(reproj - px, axis=1)

    res_norm = np.linalg.norm(res, axis=1)
    residuals = [
        {
            "x": float(px[i, 0]), "y": float(px[i, 1]),
            "wx": float(world[i, 0]), "wy": float(world[i, 1]),
            "pred_wx": float(pred[i, 0]), "pred_wy": float(pred[i, 1]),
            "res_x": float(res[i, 0]), "res_y": float(res[i, 1]),
            "res_norm": float(res_norm[i]),
            "reproj_px": float(reproj_err[i]),
        }
        for i in range(n)
    ]
    return {
        "H": H.tolist(),
        "cov_h": cov_h.tolist(),
        "sigma2": float(sigma2),
        "dof": int(dof),
        "rms": float(math.sqrt(np.mean(res_norm ** 2))),
        "max_res": float(np.max(res_norm)),
        "residuals": residuals,
    }


def apply_homography(H, pts) -> np.ndarray:
    """把 (N,2) 点经 H 变换，返回 (N,2)。"""
    H = np.asarray(H, dtype=float)
    pts = np.atleast_2d(np.asarray(pts, dtype=float))
    P = np.column_stack([pts, np.ones(len(pts))])
    q = P @ H.T
    return q[:, :2] / q[:, 2:3]


def _jacobian_point_h(H, x, y) -> np.ndarray:
    """物理坐标 (X,Y) 对 8 个单应参数的雅可比 (2,8)。"""
    D = H[2, 0] * x + H[2, 1] * y + 1.0
    X = (H[0, 0] * x + H[0, 1] * y + H[0, 2]) / D
    Y = (H[1, 0] * x + H[1, 1] * y + H[1, 2]) / D
    return np.array([
        [x / D, y / D, 1.0 / D, 0.0, 0.0, 0.0, -X * x / D, -X * y / D],
        [0.0, 0.0, 0.0, x / D, y / D, 1.0 / D, -Y * x / D, -Y * y / D],
    ])


def _jacobian_point_px(H, x, y) -> np.ndarray:
    """物理坐标 (X,Y) 对像素坐标 (x,y) 的雅可比 (2,2)。"""
    D = H[2, 0] * x + H[2, 1] * y + 1.0
    a = H[0, 0] * x + H[0, 1] * y + H[0, 2]
    b = H[1, 0] * x + H[1, 1] * y + H[1, 2]
    return np.array([
        [(H[0, 0] * D - a * H[2, 0]) / D ** 2, (H[0, 1] * D - a * H[2, 1]) / D ** 2],
        [(H[1, 0] * D - b * H[2, 0]) / D ** 2, (H[1, 1] * D - b * H[2, 1]) / D ** 2],
    ])


def world_covariance(H, cov_h, px_pts, pick_sigma_px) -> np.ndarray:
    """一组像素点变换后的物理坐标联合协方差 (2M, 2M)。

    含标定参数不确定性（各点间相关）与独立的选点噪声两部分。
    """
    H = np.asarray(H, dtype=float)
    cov_h = np.asarray(cov_h, dtype=float)
    px_pts = np.atleast_2d(np.asarray(px_pts, dtype=float))
    m = len(px_pts)
    Jh = np.zeros((2 * m, 8))
    Jp = np.zeros((2 * m, 2 * m))
    for i, (x, y) in enumerate(px_pts):
        Jh[2 * i:2 * i + 2] = _jacobian_point_h(H, x, y)
        Jp[2 * i:2 * i + 2, 2 * i:2 * i + 2] = _jacobian_point_px(H, x, y)
    return Jh @ cov_h @ Jh.T + (pick_sigma_px ** 2) * (Jp @ Jp.T)


def evaluate(H, cov_h, mtype: str, px_pts, pick_sigma_px: float) -> dict:
    """对像素点集做物理量换算。

    mtype: "points" 仅换算坐标；"line" 线段长度；"polygon" 多边形面积。
    返回 {world: [[X, Y, sigma], ...], value?, uncertainty?}，
    不确定度为 1-sigma（标准不确定度）。
    """
    H = np.asarray(H, dtype=float)
    px_pts = np.atleast_2d(np.asarray(px_pts, dtype=float))
    if mtype not in ("points", "line", "polygon"):
        raise ValueError(f"未知测量类型: {mtype}")
    if mtype == "line" and len(px_pts) != 2:
        raise ValueError("线段需要恰好 2 个点")
    if mtype == "polygon" and len(px_pts) < 3:
        raise ValueError("多边形至少需要 3 个点")

    world = apply_homography(H, px_pts)
    cov = world_covariance(H, cov_h, px_pts, pick_sigma_px)
    pt_sigma = np.sqrt(np.maximum(np.diag(cov), 0.0)).reshape(-1, 2)

    result = {
        "world": [
            [float(world[i, 0]), float(world[i, 1]),
             float(math.hypot(pt_sigma[i, 0], pt_sigma[i, 1]))]
            for i in range(len(world))
        ]
    }

    if mtype == "line":
        d = world[1] - world[0]
        L = float(math.hypot(d[0], d[1]))
        if L <= 0:
            raise ValueError("线段长度为零，无法计算")
        g = np.array([-d[0] / L, -d[1] / L, d[0] / L, d[1] / L])
        var = float(g @ cov @ g)
        result["value"] = L
        result["uncertainty"] = math.sqrt(max(var, 0.0))
    elif mtype == "polygon":
        xw, yw = world[:, 0], world[:, 1]
        area = 0.5 * float(np.sum(xw * np.roll(yw, -1) - np.roll(xw, -1) * yw))
        m = len(world)
        g = np.zeros(2 * m)
        for i in range(m):
            g[2 * i] = 0.5 * (yw[(i + 1) % m] - yw[(i - 1) % m])
            g[2 * i + 1] = 0.5 * (xw[(i - 1) % m] - xw[(i + 1) % m])
        var = float(g @ cov @ g)
        result["value"] = abs(area)
        result["uncertainty"] = math.sqrt(max(var, 0.0))
    return result
