/* 图像标定与可复核测量 —— 前端逻辑 */
"use strict";

const $ = (id) => document.getElementById(id);
const canvas = $("canvas");
const ctx = canvas.getContext("2d");

const state = {
  image: null,        // {id, filename, width, height}
  img: null,          // HTMLImageElement
  mode: "calibrate",  // calibrate | line | polygon
  calibPoints: [],    // {x, y, wx, wy, weight}
  activeCalib: null,  // {id, name, unit, pick_sigma_px}
  draft: [],          // 进行中的测量点（像素坐标）
  draftDone: false,   // 形状是否已闭合
  draftResult: null,  // 闭合后的最终计算结果
  live: null,         // 实时预览结果
  measurements: [],
  hover: null,        // 鼠标位置（图像像素坐标）
  highlightId: null,  // 列表中选中的测量
};

/* ---------------- API ---------------- */

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (e) { /* ignore */ }
    throw new Error(msg);
  }
  return res.json();
}
const getJSON = (p) => api(p);
const postJSON = (p, body) =>
  api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const del = (p) => api(p, { method: "DELETE" });

function toast(msg) { $("statusbar").textContent = msg; }
function fmt(v, digits = 4) {
  if (v === null || v === undefined) return "-";
  const a = Math.abs(v);
  if (a !== 0 && (a >= 1e6 || a < 1e-3)) return v.toExponential(3);
  return Number(v.toFixed(digits)).toString();
}

/* ---------------- 图片 ---------------- */

async function loadImages() {
  const list = await getJSON("/api/images");
  const ul = $("image-list");
  ul.innerHTML = "";
  for (const im of list) {
    const li = document.createElement("li");
    li.innerHTML = `<span>${im.filename}</span><span class="meta">${im.width}×${im.height}</span>`;
    if (state.image && im.id === state.image.id) li.classList.add("selected");
    li.onclick = () => selectImage(im.id);
    ul.appendChild(li);
  }
}

$("file-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const im = await api("/api/images", { method: "POST", body: fd });
    toast(`已上传 ${im.filename}`);
    await loadImages();
    await selectImage(im.id);
  } catch (err) { toast("上传失败: " + err.message); }
  e.target.value = "";
});

async function selectImage(id) {
  state.image = await getJSON(`/api/images/${id}`);
  state.img = new Image();
  state.img.src = `/api/images/${id}/file`;
  await new Promise((res, rej) => { state.img.onload = res; state.img.onerror = rej; });
  canvas.width = state.img.naturalWidth;
  canvas.height = state.img.naturalHeight;
  state.calibPoints = [];
  state.activeCalib = null;
  clearDraft();
  $("empty-tip").style.display = "none";
  await Promise.all([loadImages(), loadCalibrations(), loadMeasurements()]);
  setMode(state.activeCalib ? "line" : "calibrate");
  updateHeader();
  fitCanvas();
  draw();
}

/* ---------------- 标定 ---------------- */

async function loadCalibrations() {
  const list = await getJSON(`/api/images/${state.image.id}/calibrations`);
  const sel = $("calib-select");
  sel.innerHTML = "";
  const opt0 = document.createElement("option");
  opt0.value = ""; opt0.textContent = "（新建 / 未选择）";
  sel.appendChild(opt0);
  for (const c of list) {
    const o = document.createElement("option");
    o.value = c.id;
    o.textContent = `${c.name} (#${c.id}, ${c.n_points}点, RMS=${fmt(c.rms, 3)}${c.unit})`;
    sel.appendChild(o);
  }
  if (list.length && !state.activeCalib) await loadCalibration(list[0].id);
}

$("calib-select").addEventListener("change", async (e) => {
  if (e.target.value) await loadCalibration(Number(e.target.value));
});

async function loadCalibration(id) {
  const c = await getJSON(`/api/calibrations/${id}`);
  state.activeCalib = { id: c.id, name: c.name, unit: c.unit, pick_sigma_px: c.pick_sigma_px };
  state.calibPoints = c.points.map((p) => ({ ...p }));
  $("calib-name").value = c.name;
  $("calib-unit").value = c.unit;
  $("pick-sigma").value = c.pick_sigma_px;
  $("calib-select").value = String(c.id);
  renderCalibTable();
  renderResiduals(c.residuals, c.rms, c.max_res, c.unit);
  updateHeader();
  draw();
}

$("btn-delete-calib").addEventListener("click", async () => {
  const id = $("calib-select").value;
  if (!id || !confirm("删除该标定版本及其关联测量？")) return;
  await del(`/api/calibrations/${id}`);
  state.activeCalib = null;
  state.calibPoints = [];
  renderCalibTable();
  renderResiduals([], null, null, "");
  await Promise.all([loadCalibrations(), loadMeasurements()]);
  updateHeader();
  draw();
});

function renderCalibTable() {
  const tb = $("calib-table").querySelector("tbody");
  tb.innerHTML = "";
  state.calibPoints.forEach((p, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${i + 1}</td>
      <td>${p.x.toFixed(1)}</td><td>${p.y.toFixed(1)}</td>
      <td><input data-i="${i}" data-k="wx" type="number" step="any" value="${p.wx ?? ""}"></td>
      <td><input data-i="${i}" data-k="wy" type="number" step="any" value="${p.wy ?? ""}"></td>
      <td><input data-i="${i}" data-k="weight" type="number" step="any" min="0.01" value="${p.weight ?? 1}" class="w-input"></td>
      <td><button class="small danger" data-del="${i}">×</button></td>`;
    tb.appendChild(tr);
  });
  tb.querySelectorAll("input").forEach((inp) =>
    inp.addEventListener("change", (e) => {
      const i = Number(e.target.dataset.i);
      state.calibPoints[i][e.target.dataset.k] = parseFloat(e.target.value) || 0;
    }));
  tb.querySelectorAll("button[data-del]").forEach((b) =>
    b.addEventListener("click", () => {
      state.calibPoints.splice(Number(b.dataset.del), 1);
      renderCalibTable();
      draw();
    }));
}

$("btn-clear-calib").addEventListener("click", () => {
  state.calibPoints = [];
  renderCalibTable();
  draw();
});

$("btn-fit").addEventListener("click", async () => {
  if (!state.image) return;
  const pts = state.calibPoints.map((p) => ({
    x: p.x, y: p.y,
    wx: parseFloat(p.wx), wy: parseFloat(p.wy),
    weight: parseFloat(p.weight) || 1,
  }));
  if (pts.some((p) => !isFinite(p.wx) || !isFinite(p.wy))) {
    toast("请先填写所有标定点的物理坐标");
    return;
  }
  try {
    const res = await postJSON(`/api/images/${state.image.id}/calibrations`, {
      name: $("calib-name").value.trim() || "v1",
      unit: $("calib-unit").value.trim() || "mm",
      pick_sigma_px: parseFloat($("pick-sigma").value) || 0,
      points: pts,
    });
    toast(`标定已保存（版本 #${res.id}）`);
    await loadCalibrations();
    await loadCalibration(res.id);
    setMode("line");
  } catch (err) { toast("拟合失败: " + err.message); }
});

function renderResiduals(residuals, rms, maxRes, unit) {
  const tb = $("residual-table").querySelector("tbody");
  tb.innerHTML = "";
  if (!residuals || !residuals.length) {
    $("fit-summary").textContent = "";
    return;
  }
  $("fit-summary").textContent =
    `RMS 残差 = ${fmt(rms)} ${unit}，最大 |残差| = ${fmt(maxRes)} ${unit}`;
  residuals.forEach((r, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${i + 1}</td><td>${fmt(r.res_x)}</td><td>${fmt(r.res_y)}</td>
      <td>${fmt(r.res_norm)}</td><td>${fmt(r.reproj_px, 2)}</td>`;
    tb.appendChild(tr);
  });
}

/* ---------------- 测量 ---------------- */

function setMode(m) {
  state.mode = m;
  clearDraft();
  $("btn-mode-line").classList.toggle("active", m === "line");
  $("btn-mode-polygon").classList.toggle("active", m === "polygon");
  const hints = {
    calibrate: "标定模式：在画布上点击添加已知坐标点。",
    line: "线段：点击两个端点。",
    polygon: "多边形：逐点点击，双击（或点击起点附近）闭合。",
  };
  $("measure-hint").textContent =
    state.activeCalib ? hints[m] : "先完成标定，再开始测量。";
  draw();
}

$("btn-mode-line").addEventListener("click", () => setMode("line"));
$("btn-mode-polygon").addEventListener("click", () => setMode("polygon"));
$("btn-cancel-draft").addEventListener("click", clearDraft);

function clearDraft() {
  state.draft = [];
  state.draftDone = false;
  state.draftResult = null;
  state.live = null;
  $("btn-save-measure").disabled = true;
  $("live-value").textContent = "";
  draw();
}

function canvasPos(e) {
  const r = canvas.getBoundingClientRect();
  return {
    x: (e.clientX - r.left) * (canvas.width / r.width),
    y: (e.clientY - r.top) * (canvas.height / r.height),
  };
}

canvas.addEventListener("click", (e) => {
  if (!state.img) return;
  const p = canvasPos(e);
  if (state.mode === "calibrate") {
    state.calibPoints.push({ x: p.x, y: p.y, wx: "", wy: "", weight: 1 });
    renderCalibTable();
    draw();
    return;
  }
  if (!state.activeCalib) { toast("请先拟合或选择一个标定版本"); return; }
  if (state.draftDone) return;
  if (state.mode === "line") {
    state.draft.push(p);
    if (state.draft.length === 2) finishDraft();
  } else if (state.mode === "polygon") {
    // 点击起点附近则闭合（10 屏幕像素容差）
    if (state.draft.length >= 3) {
      const p0 = state.draft[0];
      if (Math.hypot(p.x - p0.x, p.y - p0.y) < 10 * viewScale()) { finishDraft(); return; }
    }
    state.draft.push(p);
  }
  draw();
});

canvas.addEventListener("dblclick", (e) => {
  if (state.mode !== "polygon" || state.draftDone) return;
  e.preventDefault();
  // 双击会先触发两次 click，去掉末尾的重复点（3 屏幕像素容差）
  const tol = 3 * viewScale();
  while (state.draft.length >= 2) {
    const a = state.draft[state.draft.length - 1];
    const b = state.draft[state.draft.length - 2];
    if (Math.hypot(a.x - b.x, a.y - b.y) < tol) state.draft.pop(); else break;
  }
  if (state.draft.length >= 3) finishDraft();
  draw();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") clearDraft();
});

async function finishDraft() {
  state.draftDone = true;
  try {
    const res = await postJSON(`/api/calibrations/${state.activeCalib.id}/evaluate`, {
      mtype: state.mode, points: state.draft.map((p) => [p.x, p.y]),
    });
    state.draftResult = res;
    showLive(res, true);
    $("btn-save-measure").disabled = false;
  } catch (err) { toast("计算失败: " + err.message); }
  draw();
}

/* 实时预览（节流） */
let previewTimer = null;
canvas.addEventListener("mousemove", (e) => {
  if (!state.img) return;
  state.hover = canvasPos(e);
  draw();
  if (previewTimer) return;
  previewTimer = setTimeout(() => { previewTimer = null; doPreview(); }, 120);
});

async function doPreview() {
  if (!state.activeCalib || !state.hover || state.draftDone) return;
  const h = state.hover;
  let mtype = "points";
  let pts = [[h.x, h.y]];
  if (state.mode === "line" && state.draft.length === 1) {
    mtype = "line";
    pts = [[state.draft[0].x, state.draft[0].y], [h.x, h.y]];
  } else if (state.mode === "polygon" && state.draft.length >= 2) {
    mtype = "polygon";
    pts = state.draft.concat([h]).map((p) => [p.x, p.y]);
  }
  try {
    const res = await postJSON(`/api/calibrations/${state.activeCalib.id}/evaluate`, { mtype, points: pts });
    state.live = res;
    const w = res.world[0];
    toast(`物理坐标: (${fmt(w[0])}, ${fmt(w[1])}) ± ${fmt(w[2])} ${res.unit}`);
    if (mtype !== "points") showLive(res, false);
  } catch (err) { /* 预览失败不打断 */ }
}

function showLive(res, final) {
  const u = state.activeCalib.unit;
  const label = state.mode === "line" ? "长度" : "面积";
  const unit = state.mode === "line" ? u : u + "²";
  $("live-value").textContent =
    `${final ? "✔ " : ""}${label} = ${fmt(res.value)} ± ${fmt(res.uncertainty)} ${unit} (1σ)`;
}

$("btn-save-measure").addEventListener("click", async () => {
  if (!state.draftDone || !state.activeCalib) return;
  try {
    const res = await postJSON(`/api/calibrations/${state.activeCalib.id}/measurements`, {
      label: $("measure-label").value.trim(),
      mtype: state.mode,
      points: state.draft.map((p) => [p.x, p.y]),
    });
    toast(`测量已保存: ${fmt(res.value)} ± ${fmt(res.uncertainty)} ${res.unit}`);
    $("measure-label").value = "";
    clearDraft();
    await loadMeasurements();
  } catch (err) { toast("保存失败: " + err.message); }
});

async function loadMeasurements() {
  state.measurements = await getJSON(`/api/images/${state.image.id}/measurements`);
  const tb = $("measure-table").querySelector("tbody");
  tb.innerHTML = "";
  for (const m of state.measurements) {
    const tr = document.createElement("tr");
    const unit = m.mtype === "line" ? m.unit : m.unit + "²";
    tr.innerHTML = `
      <td>${m.label || "—"}</td>
      <td>${m.mtype === "line" ? "线段" : "多边形"}</td>
      <td>${fmt(m.value)} ± ${fmt(m.uncertainty)} ${unit}</td>
      <td>${m.calibration_name}</td>
      <td><button class="small danger" data-del="${m.id}">×</button></td>`;
    tr.onmouseenter = () => { state.highlightId = m.id; draw(); };
    tr.onmouseleave = () => { state.highlightId = null; draw(); };
    tb.appendChild(tr);
  }
  tb.querySelectorAll("button[data-del]").forEach((b) =>
    b.addEventListener("click", async () => {
      await del(`/api/measurements/${b.dataset.del}`);
      await loadMeasurements();
      draw();
    }));
}

/* ---------------- 画布绘制 ---------------- */

function viewScale() {
  const r = canvas.getBoundingClientRect();
  if (!r.width) return 1;
  return canvas.width / r.width; // 图像像素 / 屏幕像素
}

function draw() {
  if (!state.img) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(state.img, 0, 0);
  const s = viewScale();
  const lw = Math.max(1.5, 1.5 * s);

  // 已保存测量（淡绿）
  for (const m of state.measurements) {
    const hot = m.id === state.highlightId;
    ctx.strokeStyle = hot ? "#16a34a" : "rgba(22,163,74,.45)";
    ctx.fillStyle = hot ? "rgba(22,163,74,.25)" : "rgba(22,163,74,.10)";
    ctx.lineWidth = hot ? lw * 1.6 : lw;
    drawShape(m.points.map((p) => ({ x: p[0], y: p[1] })), m.mtype === "polygon");
  }

  // 标定点（橙色编号）
  state.calibPoints.forEach((p, i) => {
    ctx.beginPath();
    ctx.arc(p.x, p.y, 5 * s, 0, 2 * Math.PI);
    ctx.fillStyle = "#f59e0b";
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = lw;
    ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.font = `${11 * s}px sans-serif`;
    ctx.textBaseline = "middle";
    ctx.textAlign = "center";
    ctx.fillText(String(i + 1), p.x, p.y + 0.5 * s);
  });

  // 进行中的测量（蓝色）
  if (state.draft.length) {
    ctx.strokeStyle = "#3b82f6";
    ctx.fillStyle = "rgba(59,130,246,.15)";
    ctx.lineWidth = lw;
    const pts = state.draft.slice();
    if (!state.draftDone && state.hover && state.mode !== "calibrate") pts.push(state.hover);
    drawShape(pts, state.mode === "polygon" && (state.draftDone || pts.length > 2));
    for (const p of state.draft) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, 4 * s, 0, 2 * Math.PI);
      ctx.fillStyle = "#3b82f6";
      ctx.fill();
    }
  }

  // 十字光标
  if (state.hover && state.mode !== "calibrate") {
    ctx.strokeStyle = "rgba(59,130,246,.6)";
    ctx.lineWidth = 1;
    const { x, y } = state.hover;
    ctx.beginPath();
    ctx.moveTo(x - 12 * s, y); ctx.lineTo(x + 12 * s, y);
    ctx.moveTo(x, y - 12 * s); ctx.lineTo(x, y + 12 * s);
    ctx.stroke();
  }
}

function drawShape(pts, closed) {
  if (pts.length < 2) return;
  ctx.beginPath();
  ctx.moveTo(pts[0].x, pts[0].y);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
  if (closed) { ctx.closePath(); ctx.fill(); }
  ctx.stroke();
}

function updateHeader() {
  $("header-status").textContent = state.activeCalib
    ? `当前标定: ${state.activeCalib.name} (#${state.activeCalib.id})，单位 ${state.activeCalib.unit}`
    : "未选择标定";
}

/* ---------------- 自适应 ---------------- */

function fitCanvas() {
  if (!state.img) return;
  const wrap = $("canvas-wrap");
  const w = Math.min(wrap.clientWidth, (wrap.clientHeight * canvas.width) / canvas.height);
  canvas.style.width = w + "px";
  canvas.style.height = (w * canvas.height) / canvas.width + "px";
  draw();
}
window.addEventListener("resize", fitCanvas);
new ResizeObserver(fitCanvas).observe($("canvas-wrap"));

/* ---------------- 启动 ---------------- */

(async function init() {
  await loadImages();
  if (state.image) return;
  const list = await getJSON("/api/images");
  if (list.length) await selectImage(list[0].id);
})();
