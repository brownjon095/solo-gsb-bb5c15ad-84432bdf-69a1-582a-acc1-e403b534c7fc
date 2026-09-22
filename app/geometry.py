"""平面透视（单应性）标定与测量的数学核心。

模型
----
单应矩阵 P 把实际平面坐标 q=(X, Y) 映射到图像像素坐标 p=(u, v)：

    [u]   [h0 h1 h2] [X]
    [v] = [h3 h4 h5] [Y],   参数向量 h = (h0..h7)，固定 h8 = 1。
    [1]   [h6 h7  1] [ 1]

标定点的像素坐标为带噪声观测，逐点给定 1σ 量测精度（像素），
以加权最小二乘拟合；先用 Hartley 归一化 + DLT 求初值，再做高斯-牛顿精化，
并由法方程逆阵得到参数协方差（dof>0 时乘 a posteriori 的 sigma0^2）。

测量（线段/多边形）的不确定度由一阶误差传播得到：
标定参数协方差与端点量测协方差分别经隐函数求导传到实际坐标，
再传到长度/面积标量。仅依赖 numpy。
"""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np


class GeometryError(ValueError):
    """标定几何构型退化或输入不合法。"""


# ---------------------------------------------------------------------------
# 基本变换
# ---------------------------------------------------------------------------

def h_vector_to_matrix(h: np.ndarray | Sequence[float]) -> np.ndarray:
    h = np.asarray(h, dtype=float).reshape(-1)
    if h.size == 8:
        h = np.concatenate([h, [1.0]])
    elif h.size != 9:
        raise GeometryError("单应参数向量必须为 8 或 9 维")
    h[8] = 1.0
    return h.reshape(3, 3)


def world_to_image(P: np.ndarray, X: float, Y: float) -> tuple[float, float]:
    """实际坐标 -> 像素坐标（P 为 world->image 单应）。"""
    v = P @ np.array([X, Y, 1.0])
    if abs(v[2]) < 1e-12:
        raise GeometryError("单应矩阵退化：目标点落在无穷远线上")
    return float(v[0] / v[2]), float(v[1] / v[2])


def image_to_world(P: np.ndarray, u: float, v: float) -> tuple[float, float]:
    """像素坐标 -> 实际坐标（内部对 P 求逆）。"""
    try:
        H = np.linalg.inv(P)
    except np.linalg.LinAlgError as exc:
        raise GeometryError("单应矩阵不可逆，标定可能退化") from exc
    w = H @ np.array([u, v, 1.0])
    if abs(w[2]) < 1e-12:
        raise GeometryError("像素点映射到无穷远，标定可能退化")
    return float(w[0] / w[2]), float(w[1] / w[2])


# ---------------------------------------------------------------------------
# 标定拟合
# ---------------------------------------------------------------------------

def _norm_transform(pts: np.ndarray) -> np.ndarray:
    """Hartley 归一化：平移到质心、缩放到平均距离 sqrt(2)。"""
    c = pts.mean(axis=0)
    d = np.linalg.norm(pts - c, axis=1).mean()
    if not np.isfinite(d) or d < 1e-12:
        raise GeometryError("控制点构型退化：所有点重合")
    s = math.sqrt(2.0) / d
    return np.array([[s, 0.0, -s * c[0]], [0.0, s, -s * c[1]], [0.0, 0.0, 1.0]])


def _dlt_initial(
    world: np.ndarray, image: np.ndarray, sigmas: np.ndarray
) -> np.ndarray:
    T_i = _norm_transform(image)
    T_w = _norm_transform(world)
    ip = (T_i @ np.column_stack([image, np.ones(len(image))]).T).T[:, :2]
    wp = (T_w @ np.column_stack([world, np.ones(len(world))]).T).T[:, :2]

    A = np.zeros((2 * len(image), 9))
    for k in range(len(image)):
        X, Y = wp[k]
        u, v = ip[k]
        wgt = 1.0 / sigmas[k]
        A[2 * k] = wgt * np.array([-X, -Y, -1, 0, 0, 0, u * X, u * Y, u])
        A[2 * k + 1] = wgt * np.array([0, 0, 0, -X, -Y, -1, v * X, v * Y, v])

    # 加权后最小奇异向量即解（需要完整 V，故 full_matrices=True）
    _, _, Vt = np.linalg.svd(A, full_matrices=True)
    Hn = Vt[-1].reshape(3, 3)
    P = np.linalg.inv(T_i) @ Hn @ T_w
    if abs(P[2, 2]) < 1e-12:
        raise GeometryError("DLT 初值退化（h8≈0），控制点可能共线")
    P = P / P[2, 2]
    # 保证控制点处分母为正（消除整体符号歧义）
    denoms = P[2, 0] * world[:, 0] + P[2, 1] * world[:, 1] + 1.0
    if np.median(np.sign(denoms[denoms != 0])) < 0:
        P = -P
    return P


def _residual_jacobian(
    h8: np.ndarray, world: np.ndarray, image: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """返回残差 r=观测-预测 (2n,) 与雅可比 J=dr/dh (2n,8)。"""
    n = len(world)
    r = np.zeros(2 * n)
    J = np.zeros((2 * n, 8))
    for k in range(n):
        X, Y = world[k]
        a = h8[0] * X + h8[1] * Y + h8[2]
        b = h8[3] * X + h8[4] * Y + h8[5]
        w = h8[6] * X + h8[7] * Y + 1.0
        if abs(w) < 1e-9:
            raise GeometryError("迭代中出现分母近零，控制点构型可能退化")
        wi = 1.0 / w
        uhat, vhat = a * wi, b * wi
        r[2 * k] = image[k, 0] - uhat
        r[2 * k + 1] = image[k, 1] - vhat

        axw, ayw = a * X * wi * wi, a * Y * wi * wi
        bxw, byw = b * X * wi * wi, b * Y * wi * wi
        J[2 * k] = [X * wi, Y * wi, wi, 0, 0, 0, -axw, -ayw]
        J[2 * k + 1] = [0, 0, 0, X * wi, Y * wi, wi, -bxw, -byw]
    return r, J


def fit_homography(
    points: Sequence[dict[str, Any]],
    default_sigma: float | None = None,
) -> dict[str, Any]:
    """加权最小二乘拟合平面透视变换。

    参数
    ----
    points: 每项 {x, y, X, Y, sigma?}
        (x, y) 像素坐标，(X, Y) 实际平面坐标，sigma 为该点像素量测 1σ（可选）。
    default_sigma: 未逐点给定时使用的统一 sigma。
    """
    n = len(points)
    if n < 4:
        raise GeometryError("透视标定至少需要 4 个已知坐标点")

    image = np.array([[float(p["x"]), float(p["y"])] for p in points], dtype=float)
    world = np.array([[float(p["X"]), float(p["Y"])] for p in points], dtype=float)
    if not (np.all(np.isfinite(image)) and np.all(np.isfinite(world))):
        raise GeometryError("坐标必须为有限数值")

    sigmas = np.array(
        [float(p["sigma"]) if p.get("sigma") is not None else float(default_sigma or 1.0)
         for p in points],
        dtype=float,
    )
    if np.any(sigmas <= 0) or not np.all(np.isfinite(sigmas)):
        raise GeometryError("点量测不确定度 sigma 必须为正数")

    P0 = _dlt_initial(world, image, sigmas)
    h = P0.reshape(9)[:8].copy()

    # 权重矩阵（每个坐标分量各占一行）
    w_diag = np.repeat(1.0 / (sigmas ** 2), 2)

    r, J = _residual_jacobian(h, world, image)
    wrss = float(np.sum(w_diag * r * r))

    converged = False
    for _ in range(50):
        JtW = J.T * w_diag
        N = JtW @ J
        g = JtW @ r
        try:
            delta = np.linalg.solve(N, g)  # 解 N δ = g（含 -J'Wr 的负号抵消）
        except np.linalg.LinAlgError:
            delta = np.linalg.lstsq(N, g, rcond=None)[0]
        if not np.all(np.isfinite(delta)):
            raise GeometryError("法方程无解，控制点构型退化")

        # 带回退的阻尼步，保证残差下降
        step = 1.0
        accepted = False
        for _ in range(12):
            try:
                r2, J2 = _residual_jacobian(h + step * delta, world, image)
            except GeometryError:
                step *= 0.5
                continue
            wrss2 = float(np.sum(w_diag * r2 * r2))
            if wrss2 <= wrss * (1 + 1e-9):
                h, r, J, wrss = h + step * delta, r2, J2, wrss2
                accepted = True
                break
            step *= 0.5
        if not accepted:
            raise GeometryError("高斯-牛顿迭代无法下降，标定构型可能退化")

        if np.max(np.abs(step * delta)) < 1e-10:
            converged = True
            break

    if not converged:
        raise GeometryError("高斯-牛顿迭代 50 次仍未收敛，请检查控制点分布")

    P = h_vector_to_matrix(h)

    dof = 2 * n - 8
    JtW = J.T * w_diag
    N = JtW @ J
    try:
        Q = np.linalg.inv(N)
    except np.linalg.LinAlgError as exc:
        raise GeometryError("法方程奇异，控制点构型退化（近共线或共点）") from exc

    if dof > 0:
        sigma0 = math.sqrt(wrss / dof)
        cov_h = (sigma0 ** 2) * Q
    else:
        # 4 点恰好定解：无多余观测，无法经验估计参数协方差
        sigma0 = None
        cov_h = np.zeros((8, 8))

    # 逐点残差：图像空间（像素）与反投影到实际平面（实际单位）
    Hinv = np.linalg.inv(P)
    per_point: list[dict[str, Any]] = []
    img_err2 = 0.0
    world_err2 = 0.0
    for k in range(n):
        a = h[0] * world[k, 0] + h[1] * world[k, 1] + h[2]
        b = h[3] * world[k, 0] + h[4] * world[k, 1] + h[5]
        wv = h[6] * world[k, 0] + h[7] * world[k, 1] + 1.0
        uhat, vhat = a / wv, b / wv
        du, dv = image[k, 0] - uhat, image[k, 1] - vhat
        d_img = math.hypot(du, dv)

        wz = Hinv @ np.array([image[k, 0], image[k, 1], 1.0])
        Xhat, Yhat = wz[0] / wz[2], wz[1] / wz[2]
        dX, dY = Xhat - world[k, 0], Yhat - world[k, 1]
        d_world = math.hypot(dX, dY)

        img_err2 += d_img ** 2
        world_err2 += d_world ** 2
        per_point.append(
            {
                "index": k,
                "name": points[k].get("name") or f"P{k + 1}",
                "du": du,
                "dv": dv,
                "image_residual": d_img,
                "dX": dX,
                "dY": dY,
                "world_residual": d_world,
            }
        )

    return {
        "h_vector": h.tolist(),
        "homography": P.tolist(),  # 注：P 为 world->image；image->world 用其逆
        "homography_world_from_image": Hinv.tolist(),
        "covariance": cov_h.tolist(),
        "sigma0": sigma0,
        "dof": dof,
        "rms_image": math.sqrt(img_err2 / n),
        "rms_world": math.sqrt(world_err2 / n),
        "point_residuals": per_point,
    }


# ---------------------------------------------------------------------------
# 测量：实际坐标与不确定度传播
# ---------------------------------------------------------------------------

def point_world_jacobian(
    P: np.ndarray, cov_h: np.ndarray, u: float, v: float
) -> tuple[np.ndarray, np.ndarray]:
    """像素点 (u,v) 的实际坐标 w 与雅可比 Jq=dw/dh (2,8)（隐函数求导）。"""
    h = P.reshape(9)
    Hinv = np.linalg.inv(P)
    q = Hinv @ np.array([u, v, 1.0])
    if abs(q[2]) < 1e-12:
        raise GeometryError("测量点映射到无穷远，标定可能退化")
    X, Y = q[0] / q[2], q[1] / q[2]

    a = h[0] * X + h[1] * Y + h[2]
    b = h[3] * X + h[4] * Y + h[5]
    w = h[6] * X + h[7] * Y + 1.0
    wi = 1.0 / w
    wi2 = wi * wi

    # 像素预测对 (X,Y) 的偏导（2x2）
    A = np.array(
        [
            [h[0] * wi - a * h[6] * wi2, h[1] * wi - a * h[7] * wi2],
            [h[3] * wi - b * h[6] * wi2, h[4] * wi - b * h[7] * wi2],
        ]
    )
    # 像素预测对参数 h 的偏导（2x8）
    B = np.array(
        [
            [X * wi, Y * wi, wi, 0, 0, 0, -a * X * wi2, -a * Y * wi2],
            [0, 0, 0, X * wi, Y * wi, wi, -b * X * wi2, -b * Y * wi2],
        ]
    )
    Jq = -np.linalg.solve(A, B)
    return np.array([X, Y]), Jq


def _segment_length(w1: np.ndarray, w2: np.ndarray) -> float:
    L = float(np.linalg.norm(w2 - w1))
    if L < 1e-15:
        raise GeometryError("相邻测量顶点重合，无法计算长度")
    return L


def evaluate_measurement(
    fit: dict[str, Any],
    pixel_points: Sequence[dict[str, float]],
    kind: str,
    endpoint_sigma: float,
    include_calibration_uncertainty: bool = True,
) -> dict[str, Any]:
    """换算线段/折线或多边形的实际长度、面积及 1σ 不确定度。

    每个顶点的实际坐标协方差取  Jq Cov(h) Jqᵀ + σ²I：
    第一项是标定参数经透视模型传播的系统分量（各顶点相关），
    第二项是端点像素量测分量（各顶点独立）。
    """
    if kind not in ("line", "polygon"):
        raise GeometryError("kind 必须为 line 或 polygon")
    if endpoint_sigma <= 0:
        raise GeometryError("端点量测不确定度必须为正数")

    m = len(pixel_points)
    if kind == "line" and m < 2:
        raise GeometryError("线段至少需要 2 个顶点")
    if kind == "polygon" and m < 3:
        raise GeometryError("多边形至少需要 3 个顶点")

    P = np.array(fit["homography"], dtype=float)
    cov_h = np.array(fit["covariance"], dtype=float) if include_calibration_uncertainty else np.zeros((8, 8))

    world_pts = np.zeros((m, 2))
    Jq_all = np.zeros((m, 2, 8))
    for i, p in enumerate(pixel_points):
        w, Jq = point_world_jacobian(P, cov_h, float(p["x"]), float(p["y"]))
        world_pts[i] = w
        Jq_all[i] = Jq

    # 每顶点 2x2 实际坐标协方差
    S = np.zeros((m, 2, 2))
    for i in range(m):
        S[i] = Jq_all[i] @ cov_h @ Jq_all[i].T + (endpoint_sigma ** 2) * np.eye(2)

    # ---- 长度 / 周长 ----
    edges = [(i, i + 1) for i in range(m - 1)]
    if kind == "polygon":
        edges.append((m - 1, 0))

    grad = np.zeros((m, 2))
    length = 0.0
    for i, j in edges:
        L = _segment_length(world_pts[i], world_pts[j])
        length += L
        unit = (world_pts[j] - world_pts[i]) / L  # i->j 单位向量
        grad[i] -= unit  # dL/dw_i
        grad[j] += unit  # dL/dw_j

    length_var = 0.0
    for i in range(m):
        length_var += grad[i] @ S[i] @ grad[i]
    length_sigma = math.sqrt(max(0.0, length_var))

    result: dict[str, Any] = {
        "kind": kind,
        "world_points": [{"X": float(world_pts[i, 0]), "Y": float(world_pts[i, 1])} for i in range(m)],
        "length": length,
        "length_sigma": length_sigma,
        "perimeter": length if kind == "polygon" else None,
        "area": None,
        "area_sigma": None,
    }

    # ---- 多边形面积（鞋带公式）----
    if kind == "polygon":
        signed_area = 0.0
        agrad = np.zeros((m, 2))
        for i in range(m):
            j = (i + 1) % m
            x_i, y_i = world_pts[i]
            x_j, y_j = world_pts[j]
            signed_area += 0.5 * (x_i * y_j - y_i * x_j)
        for i in range(m):
            p_prev = world_pts[(i - 1) % m]
            p_next = world_pts[(i + 1) % m]
            agrad[i, 0] = 0.5 * (p_next[1] - p_prev[1])
            agrad[i, 1] = 0.5 * (p_prev[0] - p_next[0])

        area_var = 0.0
        for i in range(m):
            area_var += agrad[i] @ S[i] @ agrad[i]
        result["area"] = abs(signed_area)
        result["area_sigma"] = math.sqrt(max(0.0, area_var))

    return result
