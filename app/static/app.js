"use strict";

/* ============================ 全局状态 ============================ */
const state = {
  view: "gallery",
  images: [],
  image: null,
  unit: "mm",
  mode: "gcp",
  gcps: [],                 // {name,x,y,X,Y,sigma}
  fit: null,                // 最近一次拟合结果（含 homography / residuals / covariance）
  fitDirty: true,           // GCP 改动后拟合失效
  versions: [],
  currentCalibId: null,
  draftPts: [],             // 正在绘制的测量顶点（像素）
  draftResult: null,
  measurements: [],
  selectedMeasureId: null,
  endpointSigma: 0.8,
  includeCalibUnc: true,
  // 视图变换
  scale: 1, tx: 0, ty: 0,
  img: null,
  drag: null,               // {type:'gcp'|'vert'|'pan'|'shapevert', index, moved}
  hover: null,
  needsRedraw: true,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const HIT_R = 9; // 屏幕像素命中半径

function toast(msg, isErr = false, ms = 3200) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast" + (isErr ? " error" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), ms);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

/* ============================ 视图切换 ============================ */
function showView(v) {
  state.view = v;
  $("#galleryView").classList.toggle("hidden", v !== "gallery");
  $("#workView").classList.toggle("hidden", v !== "work");
  if (v === "gallery") loadGallery();
  requestDraw();
}

$("#navGallery").addEventListener("click", () => { history.pushState({}, "", "/"); showView("gallery"); });

/* ============================ 图片库 ============================ */
async function loadGallery() {
  state.images = await api("/api/images");
  const grid = $("#galleryGrid");
  grid.innerHTML = "";
  if (!state.images.length) {
    grid.innerHTML = '<p class="dim">还没有图片，先上传一张。</p>';
    return;
  }
  for (const im of state.images) {
    const card = document.createElement("div");
    card.className = "gallery-card";
    card.innerHTML = `
      <img src="${im.url}" alt="" loading="lazy" />
      <div class="meta">
        <button class="del" title="删除">✕</button>
        <b></b>
        <span></span>
      </div>`;
    card.querySelector("b").textContent = im.name;
    card.querySelector("span").innerHTML =
      `<span class="badge">${im.width}×${im.height}</span>` +
      `<span class="badge">${im.calibration_count || 0} 个标定</span>` +
      `<span class="badge">${im.measurement_count || 0} 条测量</span>` +
      `<span class="badge">单位 ${im.unit}</span>`;
    card.querySelector("img").addEventListener("click", () => openImage(im.id));
    card.querySelector(".del").addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`删除图片「${im.name}」及其所有标定与测量？`)) return;
      await api(`/api/images/${im.id}`, { method: "DELETE" });
      toast("已删除");
      loadGallery();
    });
    grid.appendChild(card);
  }
}

$("#uploadForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = $("#fileInput").files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  fd.append("name", $("#imgName").value);
  fd.append("unit", $("#imgUnit").value);
  $("#uploadMsg").textContent = "";
  try {
    const im = await api("/api/images", { method: "POST", body: fd });
    $("#uploadForm").reset();
    toast("上传成功");
    openImage(im.id);
  } catch (err) {
    $("#uploadMsg").textContent = err.message;
  }
});

/* ============================ 打开工作区 ============================ */
async function openImage(id) {
  history.pushState({ imageId: id }, "", `#/image/${id}`);
  state.image = await api(`/api/images/${id}`);
  state.unit = state.image.unit;
  state.gcps = [];
  state.fit = null;
  state.fitDirty = true;
  state.draftPts = [];
  state.draftResult = null;
  state.selectedMeasureId = null;

  $("#imgTitle").textContent = state.image.name;
  $("#imgSize").textContent = `${state.image.width} × ${state.image.height} px · 单位 ${state.unit}`;

  state.img = new Image();
  state.img.src = state.image.url;
  state.img.onload = () => { fitView(); requestDraw(); };

  await Promise.all([loadVersions(), loadMeasurements()]);

  // 自动载入当前标定版本
  const cur = state.versions.find((v) => v.is_current) || state.versions[0];
  if (cur) await loadCalibration(cur.id);
  renderGcpTable();
  renderCalibSelect();
  updateLive();
  showView("work");
  fitView();
}

window.addEventListener("popstate", (e) => {
  if (e.state && e.state.imageId) openImage(e.state.imageId);
  else showView("gallery");
});

async function loadVersions() {
  state.versions = await api(`/api/images/${state.image.id}/calibrations`);
  state.currentCalibId = (state.versions.find((v) => v.is_current) || {}).id || null;
  renderVersions();
}

async function loadCalibration(calibId) {
  const cal = await api(`/api/calibrations/${calibId}`);
  state.unit = cal.unit || state.unit;
  if (cal.default_sigma != null) $("#defaultSigma").value = cal.default_sigma;
  state.gcps = cal.points.map((p) => ({ ...p }));
  state.fit = {
    homography: cal.homography,
    homography_world_from_image: cal.homography_world_from_image,
    covariance: cal.covariance,
    sigma0: cal.sigma0,
    dof: cal.dof ?? (state.gcps.length * 2 - 8),
    rms_image: cal.rms_image,
    rms_world: cal.rms_world,
    point_residuals: cal.residuals,
  };
  state.fitDirty = false;
  state.currentCalibId = cal.id;
  state.draftPts = [];
  state.draftResult = null;
  renderGcpTable();
  renderFit();
  renderVersions();
  renderCalibSelect();
  requestDraw();
}

/* ============================ 模式 / 工具栏 ============================ */
$$(".btn.tool").forEach((b) =>
  b.addEventListener("click", () => setMode(b.dataset.mode))
);

function setMode(mode) {
  state.mode = mode;
  $$(".btn.tool").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  const wrap = $("#canvasWrap");
  wrap.style.cursor = mode === "pan" ? "grab" : "crosshair";
  const hints = {
    gcp: "单击添加标定点 · 拖动移动 · 右键删除 · 在右侧表中填写实际坐标",
    line: "单击添加线段顶点（≥2）· 双击完成折线 · 右键删顶点 · Esc 取消",
    polygon: "单击添加多边形顶点（≥3）· 双击闭合 · 右键删顶点 · Esc 取消",
    pan: "按住拖动平移 · 滚轮缩放（所有模式下均可）",
  };
  $("#modeHint").textContent = hints[mode];
  if (mode === "gcp" || mode === "pan") $("#finishShape").disabled = true;
  else $("#finishShape").disabled = !canFinish();
  requestDraw();
}

$("#undoPoint").addEventListener("click", () => {
  if (state.mode === "gcp") state.gcps.pop();
  else state.draftPts.pop();
  state.fitDirty = true;
  renderGcpTable();
  scheduleLive();
  requestDraw();
});
$("#clearDraft").addEventListener("click", () => {
  state.draftPts = [];
  state.draftResult = null;
  updateLive();
  requestDraw();
});
$("#finishShape").addEventListener("click", () => {
  if (!canFinish()) return;
  persistMeasurement();
});
window.addEventListener("keydown", (e) => {
  if (state.view !== "work") return;
  if (e.key === "Escape") {
    state.draftPts = [];
    state.draftResult = null;
    updateLive();
    requestDraw();
  } else if (e.key === "Enter" && canFinish()) {
    persistMeasurement();
  }
});

function canFinish() {
  if (state.mode === "line") return state.draftPts.length >= 2;
  if (state.mode === "polygon") return state.draftPts.length >= 3;
  return false;
}

/* ============================ 视图变换 ============================ */
const canvas = $("#canvas");
const ctx = canvas.getContext("2d");

function resizeCanvas() {
  const wrap = $("#canvasWrap");
  const dpr = window.devicePixelRatio || 1;
  canvas.width = wrap.clientWidth * dpr;
  canvas.height = wrap.clientHeight * dpr;
  canvas.style.width = wrap.clientWidth + "px";
  canvas.style.height = wrap.clientHeight + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  requestDraw();
}
window.addEventListener("resize", resizeCanvas);

function toImage(sx, sy) {
  const r = canvas.getBoundingClientRect();
  return { x: (sx - r.left - state.tx) / state.scale, y: (sy - r.top - state.ty) / state.scale };
}
function toScreen(ix, iy) {
  return { x: ix * state.scale + state.tx, y: iy * state.scale + state.ty };
}

function fitView() {
  if (!state.img) return;
  const wrap = $("#canvasWrap");
  const pad = 30;
  const s = Math.min(
    (wrap.clientWidth - pad * 2) / state.img.width,
    (wrap.clientHeight - pad * 2) / state.img.height,
    4
  );
  state.scale = s;
  state.tx = (wrap.clientWidth - state.img.width * s) / 2;
  state.ty = (wrap.clientHeight - state.img.height * s) / 2;
  requestDraw();
}

function zoomAt(cx, cy, factor) {
  const before = toImage(cx, cy);
  const ns = Math.min(40, Math.max(0.02, state.scale * factor));
  state.scale = ns;
  const r = canvas.getBoundingClientRect();
  state.tx = cx - r.left - before.x * ns;
  state.ty = cy - r.top - before.y * ns;
  requestDraw();
}

$("#zoomIn").addEventListener("click", () => zoomAtCenter(1.25));
$("#zoomOut").addEventListener("click", () => zoomAtCenter(0.8));
$("#zoomFit").addEventListener("click", fitView);
$("#zoom100").addEventListener("click", () => zoomAtCenter(1 / state.scale));
function zoomAtCenter(f) {
  const r = canvas.getBoundingClientRect();
  zoomAt(r.left + r.width / 2, r.top + r.height / 2, f);
}

/* ============================ 指针交互 ============================ */
$("#canvasWrap").addEventListener("wheel", (e) => {
  e.preventDefault();
  zoomAt(e.clientX, e.clientY, e.deltaY < 0 ? 1.12 : 1 / 1.12);
}, { passive: false });

$("#canvasWrap").addEventListener("contextmenu", (e) => e.preventDefault());

$("#canvasWrap").addEventListener("pointerdown", (e) => {
  if (!state.img) return;
  const p = toImage(e.clientX, e.clientY);
  canvas.setPointerCapture(e.pointerId);

  if (e.button === 1 || state.mode === "pan" || e.button === 2 && state.mode === "pan") {
    state.drag = { type: "pan", sx: e.clientX, sy: e.clientY, tx0: state.tx, ty0: state.ty };
    $("#canvasWrap").style.cursor = "grabbing";
    return;
  }

  if (e.button === 2) {
    // 右键删除最近点
    if (state.mode === "gcp") {
      const hit = hitTest(p, state.gcps);
      if (hit >= 0) { state.gcps.splice(hit, 1); state.fitDirty = true; renderGcpTable(); requestDraw(); }
    } else if (state.mode === "line" || state.mode === "polygon") {
      if (state.draftPts.length) { state.draftPts.pop(); scheduleLive(); requestDraw(); }
    }
    return;
  }

  if (e.button !== 0) return;

  // 先判定拖动已有顶点
  if (state.mode === "gcp") {
    const hit = hitTest(p, state.gcps);
    if (hit >= 0) { state.drag = { type: "gcp", index: hit, moved: false }; return; }
  } else if (state.mode === "line" || state.mode === "polygon") {
    const hit = hitTest(p, state.draftPts);
    if (hit >= 0) {
      // 多边形：点击首点（≥3 个顶点）即闭合保存
      if (state.mode === "polygon" && hit === 0 && state.draftPts.length >= 3) {
        persistMeasurement();
        return;
      }
      state.drag = { type: "vert", index: hit, moved: false };
      return;
    }
  }

  // 单击添加
  if (state.mode === "gcp") {
    state.gcps.push({
      name: `P${state.gcps.length + 1}`,
      x: +p.x.toFixed(2), y: +p.y.toFixed(2), X: 0, Y: 0, sigma: null,
    });
    state.fitDirty = true;
    renderGcpTable();
    requestDraw();
  } else if (state.mode === "line" || state.mode === "polygon") {
    // 双击的第二次点击会产生几乎重复的点，吞掉它（由 dblclick 完成图形）
    const la = state._lastAdd;
    if (la && Math.hypot(la.x - p.x, la.y - p.y) < 6 / state.scale && performance.now() - la.t < 450) {
      return;
    }
    state.draftPts.push({ x: +p.x.toFixed(2), y: +p.y.toFixed(2) });
    state._lastAdd = { x: p.x, y: p.y, t: performance.now() };
    scheduleLive();
    requestDraw();
  }
});

$("#canvasWrap").addEventListener("pointermove", (e) => {
  const p = toImage(e.clientX, e.clientY);
  $("#cursorPos").textContent = `像素 ${fmt(p.x, 1)}, ${fmt(p.y, 1)}`;
  if (state.fit) {
    try {
      const w = worldFromImage(p.x, p.y);
      $("#cursorWorld").textContent = `实际 ${fmt(w[0], 4)}, ${fmt(w[1], 4)} ${state.unit}`;
    } catch { $("#cursorWorld").textContent = ""; }
  } else $("#cursorWorld").textContent = "";

  if (state.drag) {
    if (state.drag.type === "pan") {
      state.tx = state.drag.tx0 + (e.clientX - state.drag.sx);
      state.ty = state.drag.ty0 + (e.clientY - state.drag.sy);
      requestDraw();
      return;
    }
    state.drag.moved = true;
    const tgt = state.drag.type === "gcp" ? state.gcps : state.draftPts;
    tgt[state.drag.index].x = +p.x.toFixed(2);
    tgt[state.drag.index].y = +p.y.toFixed(2);
    if (state.drag.type === "gcp") {
      state.fitDirty = true;
      updateGcpRowInputs(state.drag.index);
    } else scheduleLive();
    requestDraw();
  } else {
    // hover 光标
    if (state.mode === "gcp") state.hover = hitTest(p, state.gcps);
    else if (state.mode === "line" || state.mode === "polygon") state.hover = hitTest(p, state.draftPts);
    else state.hover = null;
  }
});

$("#canvasWrap").addEventListener("pointerup", (e) => {
  if (state.drag) {
    if (state.drag.type === "gcp" && state.drag.moved) renderGcpTable();
    canvas.releasePointerCapture(e.pointerId);
    state.drag = null;
    setMode(state.mode);
  }
});

$("#canvasWrap").addEventListener("dblclick", (e) => {
  if ((state.mode === "line" || state.mode === "polygon") && canFinish()) {
    e.preventDefault();
    persistMeasurement();
  }
});

function hitTest(imgP, pts) {
  // 屏幕坐标距离判定
  let best = -1, bestD = HIT_R;
  const r = canvas.getBoundingClientRect();
  pts.forEach((q, i) => {
    const sx = q.x * state.scale + state.tx + r.left;
    const sy = q.y * state.scale + state.ty + r.top;
    const d = Math.hypot(sx - (r.left + (imgP.x * state.scale + state.tx)),
                         sy - (r.top + (imgP.y * state.scale + state.ty)));
    if (d <= bestD) { bestD = d; best = i; }
  });
  return best;
}

/* ============================ 绘制 ============================ */
function requestDraw() { state.needsRedraw = true; }
(function loop() {
  if (state.needsRedraw) { state.needsRedraw = false; draw(); }
  requestAnimationFrame(loop);
})();

function draw() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  ctx.clearRect(0, 0, w, h);
  if (!state.img) return;
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(state.img, state.tx, state.ty, state.img.width * state.scale, state.img.height * state.scale);

  // 已保存测量（浅显示）
  for (const m of state.measurements) {
    drawShape(m.points, m.kind === "polygon", "rgba(31,102,209,.45)",
      state.selectedMeasureId === m.id);
  }
  // 草稿图形
  if ((state.mode === "line" || state.mode === "polygon") && state.draftPts.length) {
    drawShape(state.draftPts, state.mode === "polygon", "#2f7de0", false, true);
  }
  // 标定点 + 残差向量
  if (state.mode === "gcp" || state.fit) drawGcps();
}

function drawShape(pts, closed, color, selected, isDraft = false) {
  if (!pts.length) return;
  ctx.save();
  ctx.beginPath();
  pts.forEach((p, i) => {
    const s = toScreen(p.x, p.y);
    i ? ctx.lineTo(s.x, s.y) : ctx.moveTo(s.x, s.y);
  });
  if (closed) ctx.closePath();
  if (closed) {
    ctx.fillStyle = selected ? "rgba(31,102,209,.18)" : "rgba(47,125,224,.08)";
    ctx.fill();
  }
  ctx.strokeStyle = selected ? "#f5a623" : color;
  ctx.lineWidth = selected ? 2.6 : 2;
  ctx.setLineDash(isDraft ? [6, 4] : []);
  ctx.stroke();
  ctx.setLineDash([]);

  pts.forEach((p, i) => {
    const s = toScreen(p.x, p.y);
    ctx.beginPath();
    ctx.arc(s.x, s.y, selected ? 5 : 4, 0, Math.PI * 2);
    ctx.fillStyle = selected ? "#f5a623" : "#2f7de0";
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 1.5;
    ctx.stroke();
    if (isDraft) {
      ctx.fillStyle = "#fff";
      ctx.font = "10px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(String(i + 1), s.x, s.y - 8);
    }
  });
  ctx.restore();
}

function drawGcps() {
  const residuals = {};
  if (state.fit && !state.fitDirty && state.fit.point_residuals) {
    state.fit.point_residuals.forEach((r) => { residuals[r.index] = r; });
  }
  state.gcps.forEach((p, i) => {
    const s = toScreen(p.x, p.y);
    // 残差箭头（观测点 -> 拟合预测点）
    const res = residuals[i];
    if (res && state.mode === "gcp") {
      // 残差箭头（观测点 -> 拟合预测点）
      const P = state.fit.homography;
      const wv = matVec(P, [p.X, p.Y, 1]);
      const ux = wv[0] / wv[2], uy = wv[1] / wv[2];
      const s2 = toScreen(ux, uy);
      ctx.beginPath();
      ctx.moveTo(s.x, s.y);
      ctx.lineTo(s2.x, s2.y);
      ctx.strokeStyle = "#f5a623";
      ctx.lineWidth = 1.4;
      ctx.stroke();
      drawArrowHead(s2.x, s2.y, Math.atan2(s2.y - s.y, s2.x - s.x));
    }

    const isHover = state.hover === i;
    const r = isHover ? 7 : 5;
    ctx.beginPath();
    ctx.arc(s.x, s.y, r, 0, Math.PI * 2);
    ctx.fillStyle = "#e0533d";
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.fillStyle = "#fff";
    ctx.strokeStyle = "rgba(0,0,0,.55)";
    ctx.lineWidth = 3;
    ctx.font = "bold 11px sans-serif";
    ctx.textAlign = "left";
    const label = p.name || `P${i + 1}`;
    ctx.strokeText(label, s.x + 8, s.y - 7);
    ctx.fillText(label, s.x + 8, s.y - 7);
  });
}

function drawArrowHead(x, y, ang) {
  const sz = 7;
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x - sz * Math.cos(ang - 0.4), y - sz * Math.sin(ang - 0.4));
  ctx.lineTo(x - sz * Math.cos(ang + 0.4), y - sz * Math.sin(ang + 0.4));
  ctx.closePath();
  ctx.fillStyle = "#f5a623";
  ctx.fill();
}

/* ============================ 矩阵小工具（实时实际坐标） ============================ */
function matVec(m, v) {
  return [
    m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
    m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
    m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
  ];
}
function worldFromImage(u, v) {
  const w = matVec(state.fit.homography_world_from_image, [u, v, 1]);
  return [w[0] / w[2], w[1] / w[2]];
}

/* ============================ GCP 表格 ============================ */
function renderGcpTable() {
  const tb = $("#gcpTable tbody");
  tb.innerHTML = "";
  state.gcps.forEach((p, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${i + 1}</td>
      <td class="namecell"><input data-k="name" value="${esc(p.name)}" style="width:48px" /></td>
      <td><input data-k="x" type="number" step="0.1" value="${p.x}" /></td>
      <td><input data-k="y" type="number" step="0.1" value="${p.y}" /></td>
      <td><input data-k="X" type="number" step="any" value="${p.X}" /></td>
      <td><input data-k="Y" type="number" step="any" value="${p.Y}" /></td>
      <td><input data-k="sigma" type="number" step="0.1" min="0.05" placeholder="默认" value="${p.sigma ?? ""}" /></td>
      <td><button class="delp" title="删除">✕</button></td>`;
    tr.querySelectorAll("input").forEach((inp) => {
      inp.addEventListener("input", () => {
        const k = inp.dataset.k;
        if (k === "name") p[k] = inp.value;
        else p[k] = inp.value === "" ? null : parseFloat(inp.value);
        if (k !== "name") state.fitDirty = true;
      });
      inp.addEventListener("change", () => {
        if (["x", "y", "X", "Y", "sigma"].includes(inp.dataset.k)) {
          state.fitDirty = true;
          if (["x", "y"].includes(inp.dataset.k)) requestDraw();
        }
      });
    });
    tr.querySelector(".delp").addEventListener("click", () => {
      state.gcps.splice(i, 1);
      state.fitDirty = true;
      renderGcpTable();
      requestDraw();
    });
    tb.appendChild(tr);
  });
  $("#fitBtn").disabled = state.gcps.length < 4;
  $("#saveCalibBtn").disabled = !(state.fit && !state.fitDirty);
}

function updateGcpRowInputs(i) {
  const tr = $("#gcpTable tbody").children[i];
  if (!tr) return;
  tr.querySelector('[data-k="x"]').value = state.gcps[i].x;
  tr.querySelector('[data-k="y"]').value = state.gcps[i].y;
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/* ============================ 拟合 ============================ */
$("#defaultSigma").addEventListener("change", () => { state.fitDirty = true; });

$("#fitBtn").addEventListener("click", doFit);

async function doFit() {
  if (state.gcps.length < 4) { toast("至少需要 4 个标定点", true); return; }
  const bad = state.gcps.find((p) => !(isFinite(p.X) && isFinite(p.Y)));
  if (bad) { toast(`点「${bad.name}」的实际坐标 X/Y 未填写`, true); return; }

  const defaultSigma = parseFloat($("#defaultSigma").value) || 0.8;
  try {
    state.fit = await api("/api/calibrations/fit", {
      method: "POST",
      body: JSON.stringify({
        image_id: state.image.id,
        points: state.gcps.map((p) => ({
          name: p.name, x: p.x, y: p.y, X: p.X, Y: p.Y,
          sigma: p.sigma ?? null,
        })),
        default_sigma: defaultSigma,
      }),
    });
    state.fitDirty = false;
    renderFit();
    $("#saveCalibBtn").disabled = false;
    toast("拟合完成，请复核残差");
    requestDraw();
  } catch (err) {
    toast("拟合失败：" + err.message, true, 6000);
  }
}

$("#saveCalibBtn").addEventListener("click", async () => {
  if (!state.fit || state.fitDirty) { toast("请先重新拟合", true); return; }
  const name = prompt("标定版本名称（可留空）：", "") ?? "";
  try {
    const cal = await api("/api/calibrations", {
      method: "POST",
      body: JSON.stringify({
        image_id: state.image.id,
        name,
        points: state.gcps,
        default_sigma: parseFloat($("#defaultSigma").value) || 0.8,
        make_current: true,
      }),
    });
    toast(`已保存为 v${cal.version}`);
    await loadVersions();
    state.currentCalibId = cal.id;
    renderVersions();
    renderCalibSelect();
  } catch (err) {
    toast("保存失败：" + err.message, true);
  }
});

function renderFit() {
  if (!state.fit) return;
  $("#fitPanel").classList.remove("hidden");
  const f = state.fit;
  const exact = (f.dof ?? (state.gcps.length * 2 - 8)) === 0;
  const sigmaNote = exact
    ? '<span class="pill warn">4 点恰好定解，无经验 σ₀</span>'
    : (f.sigma0 == null ? '<span class="pill warn">无经验 σ₀</span>' : qualityPill(f.sigma0));
  $("#fitSummary").innerHTML = `
    <div><b>图像残差 RMS</b>：${fmt(f.rms_image, 3)} px ${sigmaNote}</div>
    <div><b>实际残差 RMS</b>：${fmt(f.rms_world, 4)} ${state.unit}</div>
    <div><b>归一化 σ₀</b>：${f.sigma0 == null ? "—" : fmt(f.sigma0, 3)}
      <span class="dim">（≈1 表示与给定点 σ 相符；≫1 存在模型/量测误差）</span></div>
    <div><b>多余观测</b>：${f.dof}　<b>点数</b>：${state.gcps.length}</div>`;

  const body = $("#residBody");
  body.innerHTML = "";
  const defSigma = parseFloat($("#defaultSigma").value) || 0.8;
  f.point_residuals.forEach((r) => {
    const p = state.gcps[r.index] || {};
    const norm = r.image_residual / (p.sigma || defSigma);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="namecell">${esc(r.name)}</td>
      <td>${fmt(r.image_residual, 3)}</td>
      <td>${fmt(r.dX, 4)}</td>
      <td>${fmt(r.dY, 4)}</td>
      <td>${fmt(r.world_residual, 4)}</td>
      <td>${normPill(norm)}</td>`;
    body.appendChild(tr);
  });
}

function qualityPill(s) {
  if (s <= 1.6) return '<span class="pill good">质量良好</span>';
  if (s <= 3.0) return '<span class="pill warn">注意复核</span>';
  return '<span class="pill bad">残差过大</span>';
}
function normPill(n) {
  const cls = n <= 1.6 ? "good" : n <= 3 ? "warn" : "bad";
  return `<span class="pill ${cls}">${fmt(n, 2)}σ</span>`;
}

function renderVersions() {
  const el = $("#versionList");
  el.innerHTML = "";
  if (!state.versions.length) {
    el.innerHTML = '<p class="dim">尚无已保存标定版本</p>';
    return;
  }
  state.versions.forEach((v) => {
    const div = document.createElement("div");
    div.className = "v-item" + (v.id === state.currentCalibId ? " current" : "");
    div.innerHTML = `
      <span class="vname">v${v.version}${v.name ? " · " + esc(v.name) : ""}
        ${v.is_current ? '<span class="cur-tag">当前</span>' : ""}</span>
      <span class="rms">RMS ${fmt(v.rms_image, 2)}px</span>
      <button class="load" title="载入此版本">载入</button>
      <button class="vdel" title="删除版本">✕</button>`;
    div.querySelector(".load").addEventListener("click", (e) => {
      e.stopPropagation();
      api(`/api/calibrations/${v.id}/current`, { method: "POST" }).then(() => loadCalibration(v.id));
    });
    div.addEventListener("dblclick", () => loadCalibration(v.id));
    div.querySelector(".vdel").addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`删除标定版本 v${v.version}？已引用它的测量会保留但失去版本链接。`)) return;
      await api(`/api/calibrations/${v.id}`, { method: "DELETE" });
      await loadVersions();
      toast("已删除");
    });
    el.appendChild(div);
  });
}

/* ============================ 实时测量 ============================ */
let liveTimer = null;
function scheduleLive() {
  clearTimeout(liveTimer);
  $("#finishShape").disabled = !canFinish();
  requestDraw();
  liveTimer = setTimeout(computeLive, 120);
}

$("#endpointSigma").addEventListener("input", (e) => {
  state.endpointSigma = parseFloat(e.target.value) || 0.8;
  scheduleLive();
});
$("#includeCalibUnc").addEventListener("change", (e) => {
  state.includeCalibUnc = e.target.checked;
  scheduleLive();
});

function renderCalibSelect() {
  const sel = $("#calibSelect");
  sel.innerHTML = state.versions.length
    ? state.versions.map((v) =>
        `<option value="${v.id}" ${v.id === state.currentCalibId ? "selected" : ""}>v${v.version}${v.name ? " · " + esc(v.name) : ""}</option>`
      ).join("")
    : '<option value="">（未标定）</option>';
}

$("#calibSelect").addEventListener("change", async (e) => {
  if (e.target.value) await loadCalibration(parseInt(e.target.value, 10));
});

async function computeLive() {
  const minN = state.mode === "polygon" ? 3 : 2;
  if (state.draftPts.length < minN || !state.currentCalibId) {
    state.draftResult = null;
    updateLive();
    return;
  }
  try {
    state.draftResult = await api("/api/measurements/preview", {
      method: "POST",
      body: JSON.stringify({
        image_id: state.image.id,
        calibration_id: state.currentCalibId,
        kind: state.mode === "polygon" ? "polygon" : "line",
        points: state.draftPts,
        endpoint_sigma: state.endpointSigma,
        include_calibration_uncertainty: state.includeCalibUnc,
      }),
    });
  } catch (err) {
    state.draftResult = { error: err.message };
  }
  updateLive();
}

function updateLive() {
  $("#finishShape").disabled = !canFinish();
  $("#saveMeasureBtn").disabled = !state.draftResult || !!state.draftResult.error;
  const el = $("#liveResult");
  if (!state.currentCalibId) {
    el.innerHTML = '<p class="dim">请先在「标定」页拟合并保存至少一个标定版本。</p>';
    return;
  }
  const minN = state.mode === "polygon" ? 3 : 2;
  if (state.draftPts.length < minN) {
    el.innerHTML = `<p class="dim">${state.mode === "polygon" ? "多边形" : "线段"}至少需要 ${minN} 个顶点（当前 ${state.draftPts.length}）。</p>`;
    return;
  }
  const r = state.draftResult;
  if (!r) { el.innerHTML = '<p class="dim">计算中…</p>'; return; }
  if (r.error) { el.innerHTML = `<p style="color:var(--bad)">${esc(r.error)}</p>`; return; }
  const u = r.unit;
  let html = `
    <div class="kv"><span>${state.mode === "polygon" ? "周长" : "长度"} L</span>
      <span class="big">${fmt(r.length, 4)} ${u}</span></div>
    <div class="kv"><span>L 的 1σ 不确定度</span><span>± ${fmt(r.length_sigma, 4)} ${u}</span></div>
    <div class="kv"><span>2σ（95% 量级）</span><span>± ${fmt(2 * r.length_sigma, 4)} ${u}</span></div>
    <div class="kv"><span>相对不确定度</span><span>${fmt(100 * r.length_sigma / r.length, 3)} %</span></div>`;
  if (r.area != null) {
    html += `
      <hr style="border:none;border-top:1px solid var(--line);margin:8px 0" />
      <div class="kv"><span>面积 A</span><span class="big">${fmt(r.area, 4)} ${u}²</span></div>
      <div class="kv"><span>A 的 1σ 不确定度</span><span>± ${fmt(r.area_sigma, 4)} ${u}²</span></div>
      <div class="kv"><span>相对不确定度</span><span>${fmt(100 * r.area_sigma / r.area, 3)} %</span></div>`;
  }
  html += `<div class="dim" style="margin-top:8px">顶点实际坐标：</div>`;
  html += r.world_points.map((q, i) =>
    `<div class="dim">${i + 1}: (${fmt(q.X, 4)}, ${fmt(q.Y, 4)}) ${u}</div>`).join("");
  el.innerHTML = html;
}

async function persistMeasurement() {
  if (!state.currentCalibId) { toast("请先保存标定版本", true); return; }
  const n = state.measurements.length + 1;
  const label = prompt(
    "测量名称：",
    `${state.mode === "polygon" ? "多边形" : "线段"} ${n}`
  );
  if (label === null) return;
  try {
    await api("/api/measurements", {
      method: "POST",
      body: JSON.stringify({
        image_id: state.image.id,
        calibration_id: state.currentCalibId,
        kind: state.mode === "polygon" ? "polygon" : "line",
        label: label || `测量 ${n}`,
        points: state.draftPts,
        endpoint_sigma: state.endpointSigma,
        include_calibration_uncertainty: state.includeCalibUnc,
      }),
    });
    toast("测量已保存");
    state.draftPts = [];
    state.draftResult = null;
    await loadMeasurements();
    updateLive();
    requestDraw();
  } catch (err) {
    toast("保存失败：" + err.message, true);
  }
}

/* ============================ 已保存测量 ============================ */
async function loadMeasurements() {
  state.measurements = await api(`/api/images/${state.image.id}/measurements`);
  renderMeasurements();
}

function renderMeasurements() {
  const el = $("#measureList");
  if (!state.measurements.length) {
    el.innerHTML = '<p class="dim">尚无测量记录</p>';
    return;
  }
  el.innerHTML = "";
  state.measurements.forEach((m) => {
    const div = document.createElement("div");
    div.className = "m-item" + (m.id === state.selectedMeasureId ? " selected" : "");
    let line = `${m.kind === "polygon" ? "多边形" : "线段"} · ${m.points.length} 顶点 · 标定 v${m.calibration_version ?? "?"}`;
    let main = `L = ${fmt(m.length, 4)} ± ${fmt(m.length_sigma, 4)} ${m.unit}`;
    if (m.area != null) main += `　A = ${fmt(m.area, 4)} ± ${fmt(m.area_sigma, 4)} ${m.unit}²`;
    div.innerHTML = `
      <button class="mdel" title="删除">✕</button>
      <div class="mlabel"></div>
      <div>${main}</div>
      <div class="mline">${line}</div>`;
    div.querySelector(".mlabel").textContent = m.label;
    div.addEventListener("click", () => {
      state.selectedMeasureId = state.selectedMeasureId === m.id ? null : m.id;
      renderMeasurements();
      requestDraw();
    });
    div.querySelector(".mdel").addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`删除测量「${m.label}」？`)) return;
      await api(`/api/measurements/${m.id}`, { method: "DELETE" });
      if (state.selectedMeasureId === m.id) state.selectedMeasureId = null;
      await loadMeasurements();
      requestDraw();
    });
    el.appendChild(div);
  });
}

/* ============================ Tab / 工具 ============================ */
$$(".tab").forEach((t) =>
  t.addEventListener("click", () => {
    $$(".tab").forEach((x) => x.classList.toggle("active", x === t));
    $$(".tabpane").forEach((p) =>
      p.classList.toggle("hidden", p.dataset.tab !== t.dataset.tab)
    );
  })
);

function fmt(v, dp = 3) {
  if (v == null || !isFinite(v)) return "—";
  if (Math.abs(v) !== 0 && (Math.abs(v) >= 1e6 || Math.abs(v) < 1e-4)) return v.toExponential(dp);
  return Number(v.toFixed(dp)).toLocaleString("en-US", { maximumFractionDigits: dp });
}

/* ============================ 启动 ============================ */
resizeCanvas();
setMode("gcp");
const hash = location.hash.match(/^#\/image\/(\d+)/);
if (hash) {
  api(`/api/images/${hash[1]}`).then(() => openImage(parseInt(hash[1], 10))).catch(() => showView("gallery"));
} else {
  showView("gallery");
}
