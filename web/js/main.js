// Bootstrap: wires server state → views, status bar, code panel, screenshots.

import { app, on, emit, setSelection, setContext, savePref } from './state.js';
import { api, startSync } from './api.js';
import { fmt, esc, toast } from './ui.js';
import { docToPng } from './geometry.js';
import * as canvas from './canvas.js';
import { renderLayers, refreshInspector, renderEntities } from './panels.js';
import { updateToolbar } from './menu.js';
import { renderTabs } from './tabs.js';
import * as preview3d from './preview3d.js';

const titleEl = document.getElementById('doc-title');
const conn = document.getElementById('conn');
const st = id => document.getElementById('st-' + id);

let firstDoc = true;

on('tab-changed', () => {
  setSelection([]);
  setContext(null);
  if (!app.previewTabs.has(app.server.active) && app.view === '3d') app.view = '2d';
  emit('view-mode');
  requestAnimationFrame(canvas.zoomFit);
});

on('doc', () => {
  const s = app.server;
  canvas.render();
  renderLayers();
  renderEntities();
  refreshInspector();
  updateToolbar();
  updateTitle();
  updateStatus();
  refreshCode();
  if (firstDoc) {             // always start with the whole document in view
    firstDoc = false;
    requestAnimationFrame(canvas.zoomFit);
  }
});

on('selection', () => { updateToolbar(); updateStatus(); reportSelection(); });

// ── Selection shared with Claude (MCP get_selection / set_selection) ──

const seenSeq = {};          // tab id → last selection_seq applied
let selTimer = null;
function reportSelection() {
  clearTimeout(selTimer);
  const tab = app.server?.active, ids = [...app.selection];
  selTimer = setTimeout(() => fetch('/api/selection', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tab, ids }) }).catch(() => {}), 250);
}
on('doc', () => {
  const s = app.server;
  if (seenSeq[s.active] === undefined) { seenSeq[s.active] = s.selection_seq; return; }
  if (s.selection_seq > seenSeq[s.active]) {     // Claude selected something to show the user
    seenSeq[s.active] = s.selection_seq;
    setContext(null);
    setSelection(s.selection || []);
    if (s.selection?.length) canvas.zoomToSelection();
  }
});

// 3D preview tabs are a view, remembered per browser
try { JSON.parse(localStorage.getItem('svgcnc.previewTabs') || '[]').forEach(t => app.previewTabs.add(t)); } catch (_) {}
on('view-mode', () => localStorage.setItem('svgcnc.previewTabs', JSON.stringify([...app.previewTabs])));
on('context', () => { updateStatus(); refreshInspector(); renderEntities(); });

// ── 2D drawing ↔ 3D preview ────────────────────────────────

const canvasArea = document.querySelector('.canvas-area');
const view3d = document.getElementById('view3d');
on('view-mode', () => {
  const is3d = app.view === '3d';
  canvasArea.hidden = is3d;
  view3d.hidden = !is3d;
  document.getElementById('tools').classList.toggle('disabled', is3d);
  updateToolbar();
  if (is3d) preview3d.show();
  else requestAnimationFrame(canvas.drawRulers);
});

// ── Collapsible side panel and sections ────────────────────

const side = document.querySelector('.side');
app.sideHidden = !!JSON.parse(localStorage.getItem('svgcnc.sideHidden') || 'false');
const collapsed = new Set(JSON.parse(localStorage.getItem('svgcnc.collapsed') || '[]'));
function applySide() {
  side.hidden = app.sideHidden;
  for (const sec of side.querySelectorAll('.panel')) sec.classList.toggle('collapsed', collapsed.has(sec.id));
  updateToolbar();
  requestAnimationFrame(canvas.drawRulers);
}
document.addEventListener('toggle-side', () => {
  app.sideHidden = !app.sideHidden;
  savePref('sideHidden', app.sideHidden);
  applySide();
});
side.addEventListener('click', (e) => {
  const head = e.target.closest('.panel-head, .panel > h2:first-child');
  if (!head || e.target.closest('button')) return;
  const sec = head.closest('.panel');
  collapsed.has(sec.id) ? collapsed.delete(sec.id) : collapsed.add(sec.id);
  localStorage.setItem('svgcnc.collapsed', JSON.stringify([...collapsed]));
  applySide();
});
applySide();
on('zoom', () => { updateToolbar(); updateStatus(); });
on('view', updateToolbar);
on('active-layer', updateStatus);
on('connection', () => {
  conn.className = 'conn ' + (app.connected ? 'ok' : 'err');
  document.getElementById('conn-label').textContent = app.connected ? 'Connected' : 'Server offline — retrying';
});

function updateTitle() {
  const s = app.server;
  titleEl.innerHTML = `${esc(s.file || 'Untitled')}${s.dirty ? ' <span class="dirty" title="Unsaved changes">● edited</span>' : ''}`;
  document.title = `${s.dirty ? '• ' : ''}${s.name} — SVG CNC`;
}

let selectionGeometry = null;
on('selection-geometry', (b) => { selectionGeometry = b; updateStatus(); });
on('cursor', (p) => { st('cursor').textContent = p ? `x ${fmt(p.x, 1)}  y ${fmt(p.y, 1)} mm` : '—'; });
on('moving', ({ dx, dy }) => { st('selection').textContent = `move Δx ${fmt(dx)}  Δy ${fmt(dy)} mm`; });
on('drafting', (d) => {
  if (!d) return;
  const a = d.attrs;
  if (d.hint) { st('selection').textContent = 'click the end point, or type the size (Tab / Enter)'; return; }
  st('selection').textContent = d.tool === 'line' ? `length ${fmt(Math.hypot(a.x2 - a.x1, a.y2 - a.y1))} mm`
    : d.tool === 'rect' ? `${fmt(a.width)} × ${fmt(a.height)} mm`
    : d.tool === 'circle' ? `⌀ ${fmt(2 * a.r)} mm` : `${fmt(2 * a.rx)} × ${fmt(2 * a.ry)} mm`;
});

let measureInfo = null;
on('measure', (m) => { measureInfo = m; updateStatus(); });

function updateStatus() {
  const doc = app.doc;
  if (!doc) return;
  const n = app.selection.size, b = selectionGeometry;
  if (measureInfo) {
    st('selection').textContent = `measure ${fmt(measureInfo.len)} mm · Δx ${fmt(Math.abs(measureInfo.dx))} · Δy ${fmt(Math.abs(measureInfo.dy))}` +
      (measureInfo.done ? '  —  Enter: add as dimension · Esc: clear' : '');
  } else {
    st('selection').textContent = n ? `${n} selected${b ? ` · ${fmt(b.width)} × ${fmt(b.height)} mm at ${fmt(b.x)}, ${fmt(b.y)}` : ''}` :
      (app.context ? 'inside an entity — Esc to go up' : '');
  }
  st('layer').textContent = app.activeLayer ? `layer ${app.activeLayer}` : '';
  st('doc').textContent = `${fmt(doc.width)} × ${fmt(doc.height)} mm · ${doc.elements.length} elements`;
  st('zoom').textContent = `${Math.round(app.zoom * 100)}%`;
}

// ── SVG code panel ─────────────────────────────────────────

const codePanel = document.getElementById('code-panel');
const code = document.getElementById('code');
const codeMsg = document.getElementById('code-msg');
let codeDirty = false;

async function refreshCode(force = false) {
  if (!app.codeOpen || (codeDirty && !force)) return;
  const resp = await fetch('/api/export/file');
  code.value = await resp.text();
  codeDirty = false;
  codeMsg.textContent = 'Edit the SVG and press Apply (one undo step).';
}

document.addEventListener('toggle-code', () => {
  app.codeOpen = !app.codeOpen;
  codePanel.hidden = !app.codeOpen;
  updateToolbar();
  refreshCode(true);
  requestAnimationFrame(canvas.drawRulers);
});
code.addEventListener('input', () => { codeDirty = true; codeMsg.textContent = 'Unapplied changes'; });
document.getElementById('code-reload').addEventListener('click', () => refreshCode(true));
document.getElementById('code-apply').addEventListener('click', async () => {
  const s = await api.ops([{ op: 'replace_svg', svg: code.value }], 'Edit code');
  if (s) { codeDirty = false; toast('Applied', 'ok'); refreshCode(true); }
});

// ── Screenshots for Claude (MCP take_screenshot) ───────────

let capturing = false;
on('screenshot-requested', async () => {
  if (capturing || !app.doc) return;
  capturing = true;
  try {
    // The 3D view when it's showing, otherwise the whole 2D document
    const v = app.server?.screenshot_view || '2d';
    const shot3d = v.startsWith('3d') ? await preview3d.capture(1600, 1000, v === '3d-exploded' ? 0.8 : null) : null;
    const data = shot3d || (await docToPng(app.doc, 1800, 4)).toDataURL('image/png');
    await api.screenshot(data.split(',')[1]);
  } catch (e) {
    console.error('screenshot failed', e);
  } finally {
    capturing = false;
  }
});

// ── Start ──────────────────────────────────────────────────

document.getElementById('ruler-corner').textContent = app.unit;
document.getElementById('ruler-corner').addEventListener('click', canvas.toggleUnit);
app.zoom = +app.zoom || 1;
canvas.setTool('select');
startSync();
