"""数学核心自检：可直接用 `python tests/test_homography.py` 运行，也兼容 pytest。"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.homography import apply_homography, evaluate, fit_homography  # noqa: E402

# 一个带透视项的真实感变换：像素 -> 物理(mm)
H_TRUE = np.array([
    [2.0e-2, 1.0e-3, 1.0],
    [-1.0e-3, 2.0e-2, 2.0],
    [1.0e-6, -2.0e-6, 1.0],
])


def _grid_points():
    xs = np.linspace(50, 950, 4)
    ys = np.linspace(50, 750, 3)
    return np.array([(x, y) for y in ys for x in xs])  # 12 个点


def test_exact_fit_recovers_transform():
    px = _grid_points()
    world = apply_homography(H_TRUE, px)
    fit = fit_homography(px, world, np.ones(len(px)))
    assert fit["rms"] < 1e-8, f"rms={fit['rms']}"
    H = np.array(fit["H"])
    assert np.allclose(H, H_TRUE, atol=1e-8)

    # 无噪声 + 无选点误差 -> 不确定度应接近 0
    res = evaluate(fit["H"], fit["cov_h"], "line", [[100, 100], [600, 100]], 0.0)
    expected = np.linalg.norm(
        apply_homography(H_TRUE, [[100, 100], [600, 100]])[0]
        - apply_homography(H_TRUE, [[100, 100], [600, 100]])[1]
    )
    assert abs(res["value"] - expected) < 1e-6
    assert res["uncertainty"] < 1e-6


def test_noisy_fit_uncertainty_positive_and_calibrated():
    rng = np.random.default_rng(42)
    px = _grid_points()
    world_clean = apply_homography(H_TRUE, px)
    sigma = 0.005  # 物理坐标噪声 5 µm
    world = world_clean + rng.normal(0, sigma, world_clean.shape)
    fit = fit_homography(px, world, np.ones(len(px)))
    assert fit["rms"] > 0
    assert fit["dof"] == 2 * len(px) - 8

    # 长度不确定度应为正且量级合理（远小于长度本身）
    res = evaluate(fit["H"], fit["cov_h"], "line", [[100, 100], [900, 700]], 1.0)
    assert 0 < res["uncertainty"] < res["value"] * 0.05

    # 面积
    poly = [[100, 100], [900, 100], [900, 700], [100, 700]]
    res_a = evaluate(fit["H"], fit["cov_h"], "polygon", poly, 1.0)
    world_poly = apply_homography(H_TRUE, poly)
    x, y = world_poly[:, 0], world_poly[:, 1]
    expected_area = abs(0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    assert abs(res_a["value"] - expected_area) / expected_area < 0.01
    assert 0 < res_a["uncertainty"] < res_a["value"] * 0.05


def test_weighting_downweights_outlier():
    rng = np.random.default_rng(7)
    px = _grid_points()
    world = apply_homography(H_TRUE, px)
    world[3] += np.array([0.5, -0.4])  # 第 4 个点是粗差
    w = np.ones(len(px))
    w[3] = 0.01  # 低权重
    fit = fit_homography(px, world, w)
    # 加权后其余点的拟合应仍接近真值
    res = evaluate(fit["H"], fit["cov_h"], "points", [[500, 400]], 0.0)
    true_pt = apply_homography(H_TRUE, [[500, 400]])[0]
    got = np.array(res["world"][0][:2])
    assert np.linalg.norm(got - true_pt) < 0.02


def test_four_points_exact_solution():
    px = np.array([[0, 0], [1000, 0], [1000, 800], [0, 800]], float)
    world = apply_homography(H_TRUE, px)
    fit = fit_homography(px, world, np.ones(4))
    assert fit["dof"] == 0
    assert fit["rms"] < 1e-8
    # 4 点定解时不确定度来自回退假设，仍应给出有限值
    res = evaluate(fit["H"], fit["cov_h"], "line", [[0, 0], [1000, 800]], 1.0)
    assert np.isfinite(res["uncertainty"]) and res["uncertainty"] > 0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} 项测试全部通过")
