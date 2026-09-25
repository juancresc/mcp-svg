// Canvas view: renders the document, zoom/scroll, grid, rulers, and the tools
// (select + move + marquee, pan, line, rect, circle, ellipse, text).

import { app, emit, on, savePref, setSelection, elementById } from './state.js';
import { NS, createNode, moveAttrs, bboxOf, unionBox, boxInside, boxTouches } from './geometry.js';
import { api } from './api.js';
import { modal, toast } from './ui.js';

const svg = document.getElementById('canvas');
const wrap = document.getElementById('canvas-wrap');
const gBackground = document.getElementById('background');
const gContent = document.getElementById('content');
const gHits = document.getElementById('hits');
const gOverlay = document.getElementById('overlay');
const rulerH = document.getElementById('ruler-h');
const rulerV = document.getElementById('ruler-v');

const ZOOM_MIN = 0.02, ZOOM_MAX = 20;
const HIT_WIDTH = 12;          // px: how far from a line you can click it
const DRAG_THRESHOLD = 3;      // px before a press becomes a drag

let nodes = new Map();         // id -> rendered node
let hitNodes = new Map();      // id -> hit-area node
let gesture = null;            // current pointer interaction
let pendingRender = false;
let spaceDown = false;
let cursorDoc = null;

// ── Rendering ──────────────────────────────────────────────

export function render() {
  if (gesture?.kind === 'move') { pendingRender = true; return; }
  pendingRender = false;
  const doc = app.doc;
  if (!doc) return;

  svg.setAttribute('viewBox', `0 0 ${doc.width} ${doc.height}`);
  applyZoom();

  gBackground.replaceChildren();
  if (doc.background?.href) {
    const img = document.createElementNS(NS, 'image');
    for (const [k, v] of Object.entries({ href: doc.background.href, x: 0, y: 0, width: doc.width,
      height: doc.height, opacity: doc.background.opacity, preserveAspectRatio: 'xMidYMid meet' })) img.setAttribute(k, v);
    gBackground.appendChild(img);
  }

  nodes = new Map();
  hitNodes = new Map();
  const content = [], hits = [];
  for (const layer of doc.layers) {
    const g = document.createElementNS(NS, 'g');
    g.dataset.layer = layer.name;
    if (!layer.visible) g.setAttribute('display', 'none');
    for (const el of doc.elements) {
      if (el.layer !== layer.name) continue;
      const node = createNode(el, layer, { screen: true });
      node.dataset.id = el.id;
      if (app.selection.has(el.id)) node.classList.add('selected');
      g.appendChild(node);
      nodes.set(el.id, node);
      if (layer.visible && !layer.locked) {
        const hit = createNode(el, layer, { screen: true });
        hit.dataset.id = el.id;
        hit.removeAttribute('stroke-dasharray');
        hit.setAttribute('stroke-width', HIT_WIDTH);
        const filled = el.tag === 'text' || (el.attrs.fill && el.attrs.fill !== 'none');
        hit.setAttribute('pointer-events', filled ? 'all' : 'stroke');
        if (el.tag === 'text') hit.setAttribute('fill', 'transparent');
        hits.push(hit);
        hitNodes.set(el.id, hit);
      }
    }
    content.push(g);
  }
  gContent.replaceChildren(...content);
  gHits.replaceChildren(...hits);

  // Drop selected ids that no longer exist
  const alive = [...app.selection].filter(id => nodes.has(id));
  if (alive.length !== app.selection.size) setSelection(alive);
  else drawSelection();
}

export function drawSelection(dx = 0, dy = 0) {
  for (const [id, node] of nodes) node.classList.toggle('selected', app.selection.has(id));
  const boxes = [...app.selection].map(id => nodes.get(id) && bboxOf(nodes.get(id), svg)).filter(Boolean);
  const px = 3 / app.zoom;
  const rects = boxes.map(b => rectNode({ x: b.x - px + dx, y: b.y - px + dy, width: b.width + 2 * px, height: b.height + 2 * px }, 'sel-box'));
  gOverlay.replaceChildren(...rects);
  emit('selection-geometry', unionBox(boxes));
}

function rectNode(b, cls) {
  const r = document.createElementNS(NS, 'rect');
  r.setAttribute('x', b.x); r.setAttribute('y', b.y);
  r.setAttribute('width', Math.max(0, b.width)); r.setAttribute('height', Math.max(0, b.height));
  r.setAttribute('class', cls);
  return r;
}

export function selectionBox() {
  return unionBox([...app.selection].map(id => nodes.get(id) && bboxOf(nodes.get(id), svg)));
}

export function elementBox(id) {
  const n = nodes.get(id);
  return n ? bboxOf(n, svg) : null;
}

// ── Zoom, scroll, grid ─────────────────────────────────────

function applyZoom() {
  const doc = app.doc;
  if (!doc) return;
  svg.style.width = doc.width * app.zoom + 'px';
  svg.style.height = doc.height * app.zoom + 'px';
  applyGrid();
  drawRulers();
  emit('zoom');
}

function applyGrid() {
  if (!app.showGrid) { svg.style.backgroundImage = 'none'; return; }
  let step = app.grid;
  while (step * app.zoom < 8) step *= step % 10 === 0 || step === 1 ? 5 : 2;
  const px = step * app.zoom, major = px * 10;
  const minor = '#f0f0f0', majorC = '#e0e0e0';
  svg.style.backgroundImage = [
    `linear-gradient(to right, ${majorC} 1px, transparent 1px)`,
    `linear-gradient(to bottom, ${majorC} 1px, transparent 1px)`,
    `linear-gradient(to right, ${minor} 1px, transparent 1px)`,
    `linear-gradient(to bottom, ${minor} 1px, transparent 1px)`,
  ].join(',');
  svg.style.backgroundSize = `${major}px ${major}px, ${major}px ${major}px, ${px}px ${px}px, ${px}px ${px}px`;
}

export function setZoom(z, clientX, clientY) {
  const r = wrap.getBoundingClientRect();
  if (clientX == null) { clientX = r.left + wrap.clientWidth / 2; clientY = r.top + wrap.clientHeight / 2; }
  const before = toDoc(clientX, clientY);
  app.zoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
  savePref('zoom', app.zoom);
  applyZoom();
  const after = svg.getBoundingClientRect();
  wrap.scrollLeft += after.left + before.x * app.zoom - clientX;
  wrap.scrollTop += after.top + before.y * app.zoom - clientY;
  drawRulers();
}

export const zoomIn = () => setZoom(app.zoom * 1.25);
export const zoomOut = () => setZoom(app.zoom / 1.25);
export const zoom100 = () => setZoom(1);

export function zoomFit() {
  const d = app.doc;
  if (!d) return;
  setZoom(Math.min((wrap.clientWidth - 80) / d.width, (wrap.clientHeight - 80) / d.height));
}

export function zoomToSelection() {
  const b = selectionBox();
  if (!b) return zoomFit();
  const z = Math.min((wrap.clientWidth - 120) / Math.max(b.width, 1), (wrap.clientHeight - 120) / Math.max(b.height, 1));
  setZoom(Math.min(z, 8));
  const r = svg.getBoundingClientRect();
  wrap.scrollLeft += r.left + (b.x + b.width / 2) * app.zoom - (wrap.getBoundingClientRect().left + wrap.clientWidth / 2);
  wrap.scrollTop += r.top + (b.y + b.height / 2) * app.zoom - (wrap.getBoundingClientRect().top + wrap.clientHeight / 2);
  drawRulers();
}

export function setGrid({ snap, grid, showGrid }) {
  if (snap !== undefined) { app.snap = snap; savePref('snap', snap); }
  if (grid !== undefined) { app.grid = grid; savePref('grid', grid); }
  if (showGrid !== undefined) { app.showGrid = showGrid; savePref('showGrid', showGrid); }
  applyGrid();
  emit('view');
}

// ── Rulers (cm or in labels; document is mm) ───────────────

export function toggleUnit() {
  app.unit = app.unit === 'cm' ? 'in' : 'cm';
  savePref('unit', app.unit);
  document.getElementById('ruler-corner').textContent = app.unit;
  drawRulers();
}

export function drawRulers() {
  if (!app.doc) return;
  const svgRect = svg.getBoundingClientRect(), wrapRect = wrap.getBoundingClientRect();
  const pxPerUnit = (app.unit === 'cm' ? 10 : 25.4) * app.zoom;
  const step = [0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 50, 100].find(n => pxPerUnit * n >= 45) || 100;
  const pxMajor = pxPerUnit * step;
  const subs = [10, 5, 4, 2, 1].find(n => pxMajor / n >= 6) || 1;
  axis(rulerH, 'h', wrap.clientWidth, svgRect.left - wrapRect.left, pxMajor, step, subs, cursorDoc && cursorDoc.x * app.zoom);
  axis(rulerV, 'v', wrap.clientHeight, svgRect.top - wrapRect.top, pxMajor, step, subs, cursorDoc && cursorDoc.y * app.zoom);
}

function axis(canvas, dir, length, offset, pxMajor, step, subs, mark) {
  const T = 22, dpr = devicePixelRatio;
  const [w, h] = dir === 'h' ? [length, T] : [T, length];
  canvas.width = w * dpr; canvas.height = h * dpr;
  canvas.style.width = w + 'px'; canvas.style.height = h + 'px';
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.fillStyle = '#f7f7f7'; ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = '#bdbdbd'; ctx.fillStyle = '#777'; ctx.lineWidth = 1;
  ctx.font = '9px -apple-system, sans-serif'; ctx.textBaseline = 'top';
  const first = Math.floor(-offset / pxMajor) - 1, last = Math.ceil((length - offset) / pxMajor) + 1;
  for (let u = first; u <= last; u++) {
    for (let s = 0; s < subs; s++) {
      const p = Math.round(offset + u * pxMajor + s * pxMajor / subs) + .5;
      if (p < 0 || p > length) continue;
      const t = s === 0 ? T * .6 : (subs % 2 === 0 && s === subs / 2 ? T * .38 : T * .22);
      ctx.beginPath();
      if (dir === 'h') { ctx.moveTo(p, T); ctx.lineTo(p, T - t); } else { ctx.moveTo(T, p); ctx.lineTo(T - t, p); }
      ctx.stroke();
      if (s === 0) {
        const label = String(+(u * step).toFixed(2));
        if (dir === 'h') ctx.fillText(label, p + 2, 2);
        else { ctx.save(); ctx.translate(2, p + 2); ctx.rotate(Math.PI / 2); ctx.translate(0, -9); ctx.fillText(label, 0, 0); ctx.restore(); }
      }
    }
  }
  if (mark != null) {
    const p = offset + mark;
    ctx.strokeStyle = '#e5484d';
    ctx.beginPath();
    if (dir === 'h') { ctx.moveTo(p, 0); ctx.lineTo(p, T); } else { ctx.moveTo(0, p); ctx.lineTo(T, p); }
    ctx.stroke();
  }
}

// ── Coordinates ────────────────────────────────────────────

export function toDoc(clientX, clientY) {
  const m = svg.getScreenCTM();
  if (!m) return { x: 0, y: 0 };
  const p = new DOMPoint(clientX, clientY).matrixTransform(m.inverse());
  return { x: p.x, y: p.y };
}

const snapV = v => app.snap ? Math.round(v / app.grid) * app.grid : Math.round(v * 100) / 100;
const snapP = p => ({ x: snapV(p.x), y: snapV(p.y) });

// ── Tools ──────────────────────────────────────────────────

export function setTool(tool) {
  app.tool = tool;
  svg.className.baseVal = 'tool-' + tool;
  cancelGesture();
  emit('tool');
}

function cancelGesture() {
  if (gesture?.kind === 'move') resetMovePreview();
  if (gesture?.draft) gesture.draft.remove();
  gesture = null;
  gOverlay.querySelectorAll('.marquee').forEach(n => n.remove());
  if (pendingRender) render();
}

export function cancel() {
  if (gesture) cancelGesture();
  else if (app.selection.size) setSelection([]);
}

function activeLayerUsable() {
  const layer = app.doc.layers.find(l => l.name === app.activeLayer);
  if (!layer) { toast('Choose a layer first', 'error'); return null; }
  if (layer.locked) { toast(`Layer ${layer.name} is locked`, 'error'); return null; }
  if (!layer.visible) { toast(`Layer ${layer.name} is hidden`, 'error'); return null; }
  return layer;
}

// Pointer handling lives on the scroll area, so clicks/marquees can start outside the page
wrap.addEventListener('pointerdown', (e) => {
  if (!app.doc || e.button === 2) return;
  // preventDefault below stops the browser moving focus: release panel inputs explicitly
  if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
  if (e.target === wrap && (e.offsetX >= wrap.clientWidth || e.offsetY >= wrap.clientHeight)) return; // scrollbar
  const panning = app.tool === 'pan' || spaceDown || e.button === 1;
  if (panning) {
    gesture = { kind: 'pan', x: e.clientX, y: e.clientY, sl: wrap.scrollLeft, st: wrap.scrollTop };
    svg.classList.add('panning');
  } else if (app.tool === 'select') {
    startSelect(e);
  } else if (app.tool === 'text') {
    if (e.target !== wrap) placeText(snapP(toDoc(e.clientX, e.clientY)));
    return;
  } else {
    const layer = activeLayerUsable();
    if (!layer) return;
    const p = snapP(toDoc(e.clientX, e.clientY));
    const draft = document.createElementNS(NS, app.tool);
    draft.setAttribute('class', 'draft');
    gOverlay.appendChild(draft);
    gesture = { kind: 'draw', tool: app.tool, start: p, draft, attrs: null };
    updateDraft(p, e.shiftKey);
  }
  wrap.setPointerCapture(e.pointerId);
  e.preventDefault();
});

wrap.addEventListener('pointermove', (e) => {
  cursorDoc = toDoc(e.clientX, e.clientY);
  emit('cursor', cursorDoc);
  drawRulers();
  if (!gesture) return;
  const g = gesture;
  if (g.kind === 'pan') {
    wrap.scrollLeft = g.sl - (e.clientX - g.x);
    wrap.scrollTop = g.st - (e.clientY - g.y);
    drawRulers();
  } else if (g.kind === 'press' || g.kind === 'move') {
    if (g.kind === 'press' && Math.hypot(e.clientX - g.x, e.clientY - g.y) < DRAG_THRESHOLD) return;
    if (g.kind === 'press') beginMove(g);
    const p = toDoc(e.clientX, e.clientY);
    let dx = p.x - g.start.x, dy = p.y - g.start.y;
    if (e.shiftKey) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0; }
    if (app.snap) { dx = snapV(dx); dy = snapV(dy); }
    g.dx = dx; g.dy = dy;
    for (const id of g.ids) {
      const t = `translate(${dx}, ${dy}) ${g.orig.get(id) || ''}`.trim();
      nodes.get(id)?.setAttribute('transform', t);
      hitNodes.get(id)?.setAttribute('transform', t);
    }
    drawSelection();
    emit('moving', { dx, dy });
  } else if (g.kind === 'marquee') {
    const p = toDoc(e.clientX, e.clientY);
    const r = { x: Math.min(g.start.x, p.x), y: Math.min(g.start.y, p.y), width: Math.abs(p.x - g.start.x), height: Math.abs(p.y - g.start.y) };
    g.rect = r;
    g.touch = p.x < g.start.x;
    g.node.setAttribute('class', 'marquee' + (g.touch ? ' touch' : ''));
    for (const k of ['x', 'y', 'width', 'height']) g.node.setAttribute(k, r[k]);
  } else if (g.kind === 'draw') {
    updateDraft(snapP(toDoc(e.clientX, e.clientY)), e.shiftKey);
  }
});

wrap.addEventListener('pointerup', (e) => finishGesture(e));
wrap.addEventListener('pointercancel', () => cancelGesture());
wrap.addEventListener('pointerleave', () => { if (!gesture) { cursorDoc = null; emit('cursor', null); drawRulers(); } });

// Hover highlight follows the same closest-shape rule as clicking
let hovered = null;
svg.addEventListener('pointermove', (e) => {
  if (gesture || app.tool !== 'select') return setHover(null);
  setHover(e.target.parentNode === gHits ? pickAt(e.clientX, e.clientY) : null);
});
svg.addEventListener('pointerleave', () => setHover(null));
function setHover(id) {
  if (id === hovered) return;
  if (hovered) nodes.get(hovered)?.classList.remove('hover');
  hovered = id;
  if (id) nodes.get(id)?.classList.add('hover');
}

/** Of all shapes whose hit area is under the pointer, the one whose outline is closest. */
function pickAt(cx, cy) {
  const cands = document.elementsFromPoint(cx, cy).filter(n => n.parentNode === gHits && n.dataset.id);
  if (cands.length <= 1) return cands[0]?.dataset.id;
  let best = null, bestD = Infinity;
  for (const hit of cands) {
    const d = screenDistance(nodes.get(hit.dataset.id), cx, cy);
    if (d < bestD) { bestD = d; best = hit.dataset.id; }
  }
  return best;
}

function screenDistance(node, cx, cy) {
  if (!node || typeof node.getTotalLength !== 'function') return 6;   // text: treat as near
  const m = node.getScreenCTM();
  const len = node.getTotalLength();
  const steps = Math.min(600, Math.max(24, Math.ceil(len * app.zoom / 3)));
  let d = Infinity;
  for (let i = 0; i <= steps; i++) {
    const p = node.getPointAtLength(len * i / steps).matrixTransform(m);
    d = Math.min(d, Math.hypot(p.x - cx, p.y - cy));
  }
  const fill = node.getAttribute('fill');
  if (fill && fill !== 'none') {                     // inside a filled shape counts as close
    const local = new DOMPoint(cx, cy).matrixTransform(m.inverse());
    if (node.isPointInFill(local)) d = Math.min(d, 6);
  }
  return d;
}

function startSelect(e) {
  const id = pickAt(e.clientX, e.clientY);
  const start = toDoc(e.clientX, e.clientY);
  if (id) {
    let sel = new Set(app.selection);
    if (e.shiftKey) {
      sel.has(id) ? sel.delete(id) : sel.add(id);
      setSelection(sel);
      gesture = null;
      return;
    }
    const wasSelected = sel.has(id);
    if (!wasSelected) setSelection([id]);
    gesture = { kind: 'press', id, wasSelected, x: e.clientX, y: e.clientY, start };
  } else {
    if (!e.shiftKey) setSelection([]);
    const node = rectNode({ ...start, width: 0, height: 0 }, 'marquee');
    gOverlay.appendChild(node);
    gesture = { kind: 'marquee', start, node, additive: e.shiftKey, rect: null };
  }
}

function beginMove(g) {
  g.kind = 'move';
  g.ids = [...app.selection].filter(id => hitNodes.has(id));   // only editable layers
  g.orig = new Map(g.ids.map(id => [id, nodes.get(id)?.getAttribute('transform') || '']));
  g.dx = g.dy = 0;
}

function resetMovePreview(g = gesture) {
  for (const id of g.ids || []) {
    const t = g.orig.get(id);
    for (const n of [nodes.get(id), hitNodes.get(id)]) {
      if (!n) continue;
      t ? n.setAttribute('transform', t) : n.removeAttribute('transform');
    }
  }
}

async function finishGesture(e) {
  const g = gesture;
  if (!g) return;
  gesture = null;
  svg.classList.remove('panning');
  if (g.kind === 'press') {
    // Plain click on an already-selected element in a multi-selection: select just it
    if (g.wasSelected && app.selection.size > 1) setSelection([g.id]);
  } else if (g.kind === 'move') {
    const { dx, dy, ids } = g;
    if (!dx && !dy) { resetMovePreview(g); if (pendingRender) render(); return; }
    const ops = ids.map(id => ({ op: 'update_element', id, attrs: moveAttrs(elementById(id), dx, dy) }));
    pendingRender = true;
    await api.ops(ops, ids.length > 1 ? `Move ${ids.length} elements` : 'Move');
    render();
  } else if (g.kind === 'marquee') {
    g.node.remove();
    if (!g.rect || (g.rect.width < 1 / app.zoom && g.rect.height < 1 / app.zoom)) return;
    const hitIds = [];
    for (const [id] of hitNodes) {
      const b = bboxOf(nodes.get(id), svg);
      if (b && (g.touch ? boxTouches(b, g.rect) : boxInside(b, g.rect))) hitIds.push(id);
    }
    setSelection(g.additive ? [...app.selection, ...hitIds] : hitIds);
  } else if (g.kind === 'draw') {
    g.draft.remove();
    if (!g.attrs) return;
    const state = await api.ops([{ op: 'add_element', tag: g.tool, attrs: g.attrs, layer: app.activeLayer }], `Draw ${g.tool}`);
    if (state?.results) setSelection([state.results[0]]);
  }
  if (pendingRender) render();
}

function updateDraft(p, constrain) {
  const g = gesture, s = g.start;
  let dx = p.x - s.x, dy = p.y - s.y;
  let a = null;
  const r3 = v => Math.round(v * 1000) / 1000;
  switch (g.tool) {
    case 'line':
      if (constrain) {  // snap angle to 45°
        const ang = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4), len = Math.hypot(dx, dy);
        dx = len * Math.cos(ang); dy = len * Math.sin(ang);
      }
      if (Math.hypot(dx, dy) >= 0.5) a = { x1: s.x, y1: s.y, x2: r3(s.x + dx), y2: r3(s.y + dy) };
      break;
    case 'rect':
      if (constrain) { const m = Math.max(Math.abs(dx), Math.abs(dy)); dx = Math.sign(dx || 1) * m; dy = Math.sign(dy || 1) * m; }
      if (Math.abs(dx) >= 0.5 && Math.abs(dy) >= 0.5)
        a = { x: r3(Math.min(s.x, s.x + dx)), y: r3(Math.min(s.y, s.y + dy)), width: r3(Math.abs(dx)), height: r3(Math.abs(dy)) };
      break;
    case 'circle': {
      const r = r3(Math.hypot(dx, dy));
      if (r >= 0.25) a = { cx: s.x, cy: s.y, r };
      break;
    }
    case 'ellipse':
      if (constrain) { const m = Math.max(Math.abs(dx), Math.abs(dy)); dx = dy = m; }
      if (Math.abs(dx) >= 0.25 && Math.abs(dy) >= 0.25) a = { cx: s.x, cy: s.y, rx: r3(Math.abs(dx)), ry: r3(Math.abs(dy)) };
      break;
  }
  g.attrs = a;
  if (a) for (const [k, v] of Object.entries(a)) g.draft.setAttribute(k, v);
  emit('drafting', a && { tool: g.tool, attrs: a });
}

async function placeText(p) {
  const layer = activeLayerUsable();
  if (!layer) return;
  let values = null;
  const ok = await modal({
    title: 'Add text',
    html: `<div class="kv"><label>Text</label><input name="text" class="field" value="Text">
      <label>Size (mm)</label><input name="size" type="number" class="field" value="10" min="0.5" step="0.5"></div>
      <p class="hint" style="margin-top:10px">Text is for NOTES/labels. For engraving, CAM needs text converted to paths.</p>`,
    buttons: [{ label: 'Cancel', value: false }, { label: 'Add', value: true, kind: 'primary' }],
    onSubmit: (form) => { values = Object.fromEntries(new FormData(form)); return !!values.text.trim(); },
  });
  if (!ok) return;
  const state = await api.ops([{ op: 'add_element', tag: 'text', layer: app.activeLayer, text: values.text,
    attrs: { x: p.x, y: p.y, 'font-size': values.size || 10, 'font-family': 'sans-serif' } }], 'Add text');
  if (state?.results) setSelection([state.results[0]]);
}

// ── Keyboard helpers used by main ──────────────────────────

export function setSpace(down) {
  spaceDown = down;
  svg.classList.toggle('panning', down && app.tool !== 'pan');
}

// Ctrl/⌘ + wheel or trackpad pinch → zoom around the cursor
wrap.addEventListener('wheel', (e) => {
  if (!e.ctrlKey && !e.metaKey) return;
  e.preventDefault();
  setZoom(app.zoom * Math.exp(-e.deltaY * 0.01), e.clientX, e.clientY);
}, { passive: false });
wrap.addEventListener('scroll', drawRulers);
window.addEventListener('resize', drawRulers);
on('selection', drawSelection);
