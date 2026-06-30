/* magic-redact web UI — vanilla JS, no build step.
 *
 * Flow: upload -> draw image on a <canvas> -> /detect -> overlay clickable region
 * boxes (color-coded by kind). Hover highlights; click one box -> /redact only that
 * region. "Redact all" redacts every region. Manual mode lets the user draw boxes
 * when /detect finds nothing. The synthetic identity is shown in the side panel and
 * can be re-rolled (a seed keeps it reproducible).
 */
"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  file: null,          // the uploaded File
  img: new Image(),    // decoded image element
  natW: 0, natH: 0,    // natural pixel size
  scale: 1,            // canvas-display scale (display px / natural px)
  regions: [],         // [{id, kind, bbox:[x,y,w,h], field, text}]
  hover: null,         // hovered region id
  seed: null,          // identity seed (re-roll changes it)
  identity: null,      // last identity dict from a /redact or /identity call
  manual: false,       // manual-box mode
  // manual drawing scratch
  drawing: false, dragStart: null, dragRect: null,
  lastResultUrl: null,
};

const KIND_COLOR = { face: "#ff5d8f", text: "#4f8cff", mrz: "#f5b942" };

const canvas = $("canvas");
const ctx = canvas.getContext("2d");

/* ----------------------------------------------------------------------- */
/* boot: health + first identity                                            */
/* ----------------------------------------------------------------------- */
async function boot() {
  try {
    const h = await fetch("/healthz").then((r) => r.json());
    $("dot-detector").classList.add(h.detector_available ? "on" : "off");
    $("dot-qwen").classList.add(h.qwen_available ? "on" : "off");
    if (!h.qwen_available) $("qwen-opt-wrap").classList.add("hidden");
  } catch (e) { /* health is best-effort */ }
  loadGallery();
  await reroll();
}

/* ----------------------------------------------------------------------- */
/* gallery of specimen test documents (samples/images)                      */
/* ----------------------------------------------------------------------- */
async function loadGallery() {
  const grid = $("gallery-grid");
  try {
    const data = await fetch("/samples").then((r) => r.json());
    const items = data.items || [];
    if (!items.length) { $("gallery").classList.add("hidden"); return; }
    $("gallery-count").textContent = `(${items.length})`;
    grid.innerHTML = "";
    for (const it of items) {
      const card = document.createElement("button");
      card.className = "gthumb";
      card.title = (it.title || it.file) + (it.license ? ` — ${it.license}` : "");
      const img = document.createElement("img");
      img.loading = "lazy"; img.src = it.url; img.alt = "";
      const cap = document.createElement("span");
      cap.className = "gcap"; cap.textContent = galleryLabel(it);
      card.append(img, cap);
      card.addEventListener("click", () => loadFromUrl(it.url, it.file));
      grid.appendChild(card);
    }
    $("gallery").classList.remove("hidden");
  } catch (e) { $("gallery").classList.add("hidden"); }
}

function galleryLabel(it) {
  const t = (it.title || it.file || "").replace(/^File:/, "").replace(/\.[a-z0-9]+$/i, "");
  return t.length > 30 ? t.slice(0, 28) + "…" : t;
}

async function loadFromUrl(url, name) {
  try {
    const blob = await fetch(url).then((r) => r.blob());
    loadFile(new File([blob], name || "sample.png", { type: blob.type || "image/png" }));
  } catch (e) { toast("Could not load that document.", true); }
}

/* ----------------------------------------------------------------------- */
/* upload / drag-drop                                                        */
/* ----------------------------------------------------------------------- */
const dz = $("dropzone");
$("browse").addEventListener("click", () => $("file").click());
dz.addEventListener("click", (e) => { if (e.target.tagName !== "BUTTON") $("file").click(); });
$("file").addEventListener("change", (e) => { if (e.target.files[0]) loadFile(e.target.files[0]); });
["dragover", "dragenter"].forEach((ev) =>
  dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) =>
  dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
dz.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files[0];
  if (f && f.type.startsWith("image/")) loadFile(f);
});

function loadFile(file) {
  state.file = file;
  state.regions = [];
  state.manual = false;
  const url = URL.createObjectURL(file);
  state.img.onload = () => {
    state.natW = state.img.naturalWidth;
    state.natH = state.img.naturalHeight;
    $("dropzone").classList.add("hidden");
    $("gallery").classList.add("hidden");
    $("stage").classList.remove("hidden");
    fitCanvas();
    draw();
  };
  state.img.src = url;
}

function fitCanvas() {
  const maxW = canvas.parentElement.clientWidth || 800;
  state.scale = Math.min(1, maxW / state.natW);
  canvas.width = Math.round(state.natW * state.scale);
  canvas.height = Math.round(state.natH * state.scale);
}

/* ----------------------------------------------------------------------- */
/* drawing the canvas + overlays                                            */
/* ----------------------------------------------------------------------- */
function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(state.img, 0, 0, canvas.width, canvas.height);
  for (const r of state.regions) drawRegion(r);
  if (state.dragRect) drawRect(state.dragRect, "#2bd4a4", true);
}

function drawRegion(r) {
  const s = state.scale;
  const [x, y, w, h] = r.bbox;
  const color = KIND_COLOR[r.kind] || "#888";
  const hovered = state.hover === r.id;
  ctx.lineWidth = hovered ? 3 : 2;
  ctx.strokeStyle = color;
  ctx.fillStyle = color + (hovered ? "33" : "1a");
  ctx.fillRect(x * s, y * s, w * s, h * s);
  ctx.strokeRect(x * s, y * s, w * s, h * s);
  // label chip
  const label = (r.field && r.field !== "unknown") ? r.field : r.kind;
  ctx.font = "11px Segoe UI, sans-serif";
  const tw = ctx.measureText(label).width + 8;
  ctx.fillStyle = color;
  ctx.fillRect(x * s, Math.max(0, y * s - 15), tw, 15);
  ctx.fillStyle = "#0b0d11";
  ctx.fillText(label, x * s + 4, Math.max(11, y * s - 4));
}

function drawRect(rect, color, dashed) {
  const s = state.scale;
  ctx.save();
  if (dashed) ctx.setLineDash([6, 4]);
  ctx.lineWidth = 2; ctx.strokeStyle = color;
  ctx.strokeRect(rect[0] * s, rect[1] * s, rect[2] * s, rect[3] * s);
  ctx.restore();
}

/* hit-test in natural coords */
function regionAt(nx, ny) {
  // last drawn wins (topmost)
  for (let i = state.regions.length - 1; i >= 0; i--) {
    const [x, y, w, h] = state.regions[i].bbox;
    if (nx >= x && nx <= x + w && ny >= y && ny <= y + h) return state.regions[i];
  }
  return null;
}

function toNatural(evt) {
  const rect = canvas.getBoundingClientRect();
  const px = (evt.clientX - rect.left) * (canvas.width / rect.width);
  const py = (evt.clientY - rect.top) * (canvas.height / rect.height);
  return [px / state.scale, py / state.scale];
}

/* ----------------------------------------------------------------------- */
/* canvas interaction: hover, click-to-redact-one, manual draw              */
/* ----------------------------------------------------------------------- */
canvas.addEventListener("mousemove", (e) => {
  const [nx, ny] = toNatural(e);
  if (state.drawing && state.dragStart) {
    const [sx, sy] = state.dragStart;
    state.dragRect = [Math.min(sx, nx), Math.min(sy, ny), Math.abs(nx - sx), Math.abs(ny - sy)];
    draw();
    return;
  }
  const r = regionAt(nx, ny);
  const newHover = r ? r.id : null;
  if (newHover !== state.hover) { state.hover = newHover; draw(); }
  canvas.style.cursor = state.manual ? "crosshair" : (r ? "pointer" : "default");
});

canvas.addEventListener("mousedown", (e) => {
  if (!state.manual) return;
  const [nx, ny] = toNatural(e);
  state.drawing = true;
  state.dragStart = [nx, ny];
});

window.addEventListener("mouseup", (e) => {
  if (state.drawing) {
    state.drawing = false;
    if (state.dragRect && state.dragRect[2] > 6 && state.dragRect[3] > 6) {
      addManualRegion(state.dragRect);
    }
    state.dragRect = null; state.dragStart = null;
    draw();
  }
});

canvas.addEventListener("click", (e) => {
  if (state.manual) return; // manual mode draws, doesn't redact-on-click
  const [nx, ny] = toNatural(e);
  const r = regionAt(nx, ny);
  if (r) redactRegions([r.id]);
});

function addManualRegion(rect) {
  const field = $("manual-field").value;
  const kind = field === "photo" ? "face" : (field === "mrz" ? "mrz" : "text");
  const id = "m" + Date.now().toString(36);
  state.regions.push({
    id, kind,
    bbox: [Math.round(rect[0]), Math.round(rect[1]), Math.round(rect[2]), Math.round(rect[3])],
    field, text: null,
  });
  toast(`Added ${field} box — click "Redact all" or the box to redact.`);
}

/* ----------------------------------------------------------------------- */
/* detect                                                                    */
/* ----------------------------------------------------------------------- */
$("btn-detect").addEventListener("click", detect);
async function detect() {
  if (!state.file) return;
  busy($("btn-detect"), true, "Detecting…");
  try {
    const fd = new FormData();
    fd.append("image", state.file);
    const res = await fetch("/detect", { method: "POST", body: fd });
    const data = await res.json();
    state.regions = (data.regions || []).map((r) => ({
      id: r.id, kind: r.kind, bbox: r.bbox, field: r.field, text: r.text,
    }));
    if (state.regions.length === 0) {
      enterManualMode(data.detector_available === false
        ? "No detector installed — draw boxes manually."
        : "Nothing auto-detected — draw boxes manually.");
    } else {
      state.manual = false;
      $("manual-bar").classList.add("hidden");
      toast(`Detected ${state.regions.length} region(s).`);
    }
    draw();
  } catch (e) {
    enterManualMode("Detection failed — draw boxes manually.");
    draw();
  } finally {
    busy($("btn-detect"), false, "Detect regions");
  }
}

function enterManualMode(msg) {
  state.manual = true;
  $("manual-bar").classList.remove("hidden");
  document.querySelector(".manual-msg").textContent = msg;
}

$("btn-clear-manual").addEventListener("click", () => {
  state.regions = []; draw();
});

/* ----------------------------------------------------------------------- */
/* redact                                                                    */
/* ----------------------------------------------------------------------- */
$("btn-redact-all").addEventListener("click", () => {
  if (state.regions.length === 0) { toast("No regions to redact — detect or draw boxes first.", true); return; }
  redactRegions(null);
});

async function redactRegions(onlyIds) {
  if (!state.file || state.regions.length === 0) return;
  const btn = onlyIds ? null : $("btn-redact-all");
  if (btn) busy(btn, true, "Redacting…");
  try {
    const fd = new FormData();
    fd.append("image", state.file);
    fd.append("regions", JSON.stringify(state.regions));
    fd.append("watermark", $("opt-watermark").checked ? "true" : "false");
    if (state.seed != null) fd.append("identity_seed", String(state.seed));
    if (onlyIds) fd.append("only", JSON.stringify(onlyIds));
    if ($("opt-qwen") && $("opt-qwen").checked) fd.append("face_source", "qwen");

    const res = await fetch("/redact", { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }));
      toast("Redact failed: " + (err.error || res.status), true);
      return;
    }
    // identity from response header
    const idHeader = res.headers.get("X-Identity");
    if (idHeader) {
      try { renderIdentity(JSON.parse(atob(idHeader))); } catch (e) {}
    }
    const blob = await res.blob();
    showResult(blob);
    toast(onlyIds ? "Region redacted." : "All regions redacted.");
  } catch (e) {
    toast("Redact error: " + e.message, true);
  } finally {
    if (btn) busy(btn, false, "Redact all");
  }
}

function showResult(blob) {
  if (state.lastResultUrl) URL.revokeObjectURL(state.lastResultUrl);
  const url = URL.createObjectURL(blob);
  state.lastResultUrl = url;
  $("result-wrap").innerHTML = `<img src="${url}" alt="redacted result (SPECIMEN watermarked)" />`;
  const dl = $("btn-download");
  dl.href = url;
  dl.classList.remove("disabled");
}

/* ----------------------------------------------------------------------- */
/* identity panel + re-roll                                                  */
/* ----------------------------------------------------------------------- */
$("btn-reroll").addEventListener("click", reroll);
async function reroll() {
  state.seed = Math.floor(Math.random() * 1e9);
  const fd = new FormData();
  fd.append("seed", String(state.seed));
  try {
    const idn = await fetch("/identity", { method: "POST", body: fd }).then((r) => r.json());
    renderIdentity(idn);
  } catch (e) { /* identity is best-effort */ }
}

function renderIdentity(idn) {
  state.identity = idn;
  const name = `${(idn.given_names || "").toUpperCase()} ${(idn.surname || "").toUpperCase()}`.trim();
  $("id-name").textContent = name || "—";
  $("id-sex").textContent = idn.sex || "—";
  $("id-dob").textContent = idn.dob || "—";
  const nat = idn.nationality ? `${idn.nationality.name} (${idn.nationality.iso3})` : "—";
  $("id-nat").textContent = nat;
  $("id-doc").textContent = idn.doc_number || "—";
  $("id-exp").textContent = idn.expiry || "—";
  $("id-mrz").textContent = Array.isArray(idn.mrz) ? idn.mrz.join("\n") : (idn.mrz || "—");
  $("id-seed").textContent = idn.seed != null ? idn.seed : (state.seed ?? "—");
}

/* ----------------------------------------------------------------------- */
/* reset / misc                                                              */
/* ----------------------------------------------------------------------- */
$("btn-reset").addEventListener("click", () => {
  state.file = null; state.regions = []; state.manual = false;
  $("stage").classList.add("hidden");
  $("dropzone").classList.remove("hidden");
  $("gallery").classList.toggle("hidden", $("gallery-grid").children.length === 0);
  $("manual-bar").classList.add("hidden");
  $("result-wrap").innerHTML = `<p class="result-empty">Redact a region to see the watermarked result here.</p>`;
  $("btn-download").classList.add("disabled");
  $("file").value = "";
});

let toastTimer = null;
function toast(msg, isErr) {
  let t = $("toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "toast"; t.className = "toast";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = "toast"; }, 3200);
}

function busy(btn, on, label) {
  if (!btn) return;
  btn.disabled = on;
  if (on) {
    btn.dataset.label = btn.textContent;
    btn.innerHTML = `<span class="spinner"></span> ${label || "…"}`;
  } else {
    btn.textContent = label || btn.dataset.label || "Done";
  }
}

window.addEventListener("resize", () => { if (state.file) { fitCanvas(); draw(); } });
boot();
