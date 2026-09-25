// Bootstrap: wires server state → views, status bar, code panel, screenshots.

import { app, on } from './state.js';
import { api, startSync } from './api.js';
import { fmt, esc, toast } from './ui.js';
import { docToPng } from './geometry.js';
import * as canvas from './canvas.js';
import { renderLayers, refreshInspector } from './panels.js';
import { updateToolbar } from './menu.js';

const titleEl = document.getElementById('doc-title');
const conn = document.getElementById('conn');
const st = id => document.getElementById('st-' + id);

let firstDoc = true;

on('doc', () => {
  const s = app.server;
  canvas.render();
  renderLayers();
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

on('selection', () => { updateToolbar(); updateStatus(); });
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
  st('selection').textContent = d.tool === 'line' ? `length ${fmt(Math.hypot(a.x2 - a.x1, a.y2 - a.y1))} mm`
    : d.tool === 'rect' ? `${fmt(a.width)} × ${fmt(a.height)} mm`
    : d.tool === 'circle' ? `⌀ ${fmt(2 * a.r)} mm` : `${fmt(2 * a.rx)} × ${fmt(2 * a.ry)} mm`;
});

function updateStatus() {
  const doc = app.doc;
  if (!doc) return;
  const n = app.selection.size, b = selectionGeometry;
  st('selection').textContent = n ? `${n} selected${b ? ` · ${fmt(b.width)} × ${fmt(b.height)} mm at ${fmt(b.x)}, ${fmt(b.y)}` : ''}` : '';
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
    const c = await docToPng(app.doc, 1800, 4);
    await api.screenshot(c.toDataURL('image/png').split(',')[1]);
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
