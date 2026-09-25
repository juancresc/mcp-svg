// User actions shared by the menus, toolbar, keyboard shortcuts and panels.

import { app, setSelection, selectedElements, elementById } from './state.js';
import { api } from './api.js';
import { modal, confirmDialog, toast, esc, download, pickFile, fmt } from './ui.js';
import { moveAttrs, docToPng } from './geometry.js';
import * as canvas from './canvas.js';

// ── Unsaved changes ────────────────────────────────────────

/** Returns true if it's OK to replace the document (saved, discarded, or clean). */
async function resolveUnsaved(verb) {
  if (!app.server?.dirty) return { ok: true, discard: false };
  const choice = await modal({
    title: `Save changes to “${app.server.name}”?`,
    html: `<p>Your changes will be lost if you ${verb} without saving.</p>`,
    buttons: [
      { label: "Don't save", value: 'discard', kind: 'danger', left: true },
      { label: 'Cancel', value: null },
      { label: 'Save', value: 'save', kind: 'primary' },
    ],
  });
  if (choice === 'save') return { ok: await save(), discard: false };
  if (choice === 'discard') return { ok: true, discard: true };
  return { ok: false };
}

// ── File ───────────────────────────────────────────────────

const PRESETS = [
  ['Sheet 2440 × 1220', 2440, 1220], ['Sheet 2500 × 1250', 2500, 1250], ['Half sheet 1220 × 1220', 1220, 1220],
  ['1220 × 610', 1220, 610], ['A3 420 × 297', 420, 297], ['A4 297 × 210', 297, 210],
];
const UNIT_MM = { mm: 1, cm: 10, in: 25.4 };

function sizeFields(w, h) {
  return `<div class="kv">
      <label>Width</label><input name="w" type="number" class="field" step="any" min="0.1" value="${w}" required>
      <label>Height</label><input name="h" type="number" class="field" step="any" min="0.1" value="${h}" required>
      <label>Units</label><select name="unit" class="field"><option>mm</option><option>cm</option><option>in</option></select>
    </div>
    <div class="presets">${PRESETS.map(([n, pw, ph]) => `<button type="button" class="btn" data-w="${pw}" data-h="${ph}">${n}</button>`).join('')}</div>`;
}

function bindPresets(form) {
  form.querySelectorAll('.presets .btn').forEach(b => b.addEventListener('click', () => {
    form.w.value = b.dataset.w; form.h.value = b.dataset.h; form.unit.value = 'mm';
  }));
}

function readSize(form) {
  const k = UNIT_MM[form.unit.value];
  const w = +form.w.value * k, h = +form.h.value * k;
  if (!(w > 0 && h > 0)) { toast('Enter a width and height', 'error'); return null; }
  return { w: Math.round(w * 1000) / 1000, h: Math.round(h * 1000) / 1000 };
}

export async function newDocument() {
  const u = await resolveUnsaved('start a new document');
  if (!u.ok) return;
  let size = null;
  const ok = await modal({
    title: 'New document',
    html: sizeFields(2440, 1220) + '<p class="hint" style="margin-top:10px">1 unit = 1 mm. Starts with the default CNC layers.</p>',
    buttons: [{ label: 'Cancel', value: false }, { label: 'Create', value: true, kind: 'primary' }],
    setup: bindPresets,
    onSubmit: (form) => !!(size = readSize(form)),
  });
  if (!ok) return;
  if (await api.newDoc(size.w, size.h, true)) {
    setSelection([]);
    canvas.zoomFit();
  }
}

export async function openDocument() {
  let listing;
  try { listing = await api.files(); } catch (e) { return toast(e.message, 'error'); }
  let chosen = null;
  const result = await modal({
    title: 'Open',
    html: listing.files.length
      ? `<div class="file-list">${listing.files.map(f => `<button type="button" class="file-item" data-file="${esc(f.file)}">
          <span>${esc(f.file)}</span>${f.file === listing.open ? '<span class="current">open</span>' : ''}
          <span class="meta">${new Date(f.modified * 1000).toLocaleString()} · ${fmt(f.size / 1024, 1)} KB</span></button>`).join('')}</div>`
      : '<p class="muted">No documents saved yet in the data folder.</p>',
    buttons: [{ label: 'Upload from computer…', value: 'upload', left: true }, { label: 'Cancel', value: null },
      { label: 'Open', value: 'open', kind: 'primary' }],
    setup: (form, close) => form.querySelectorAll('.file-item').forEach(b => {
      b.addEventListener('click', () => {
        form.querySelectorAll('.file-item').forEach(x => x.classList.remove('selected'));
        b.classList.add('selected');
        chosen = b.dataset.file;
      });
      b.addEventListener('dblclick', () => { chosen = b.dataset.file; close('open'); });
    }),
    onSubmit: () => { if (!chosen) { toast('Pick a file'); return false; } },
  });
  if (result === 'upload') return uploadDocument();
  if (result !== 'open' || !chosen) return;
  const u = await resolveUnsaved('open another document');
  if (!u.ok) return;
  if (await api.open(chosen, true)) {
    setSelection([]);
    canvas.zoomFit();
    toast(`Opened ${chosen}`, 'ok');
  }
}

async function uploadDocument() {
  const file = await pickFile('.svg,image/svg+xml');
  if (!file) return;
  const u = await resolveUnsaved('open another document');
  if (!u.ok) return;
  const name = file.name.replace(/\.svg$/i, '');
  if (await api.importSvg(await file.text(), { replace: true, discard: true })) {
    setSelection([]);
    canvas.zoomFit();
    toast(`Loaded ${file.name} — use Save As to keep it in the data folder (suggested: ${name})`);
  }
}

export async function save() {
  if (!app.server.file) return saveAs();
  const s = await api.save();
  if (s) toast(`Saved ${s.file}`, 'ok');
  return !!s;
}

export async function saveAs(initialName) {
  let listing = { files: [] };
  try { listing = await api.files(); } catch (_) {}
  const existing = new Set(listing.files.map(f => f.file));
  let name = null;
  const suggestion = initialName || (app.server.file ? app.server.file.replace(/\.svg$/, '') : 'untitled');
  const ok = await modal({
    title: 'Save As',
    html: `<div class="kv"><label>File name</label><input name="name" class="field" value="${esc(suggestion)}"></div>
      <p class="hint" style="margin-top:8px">Saved in the data folder as SVG (mm units, Inkscape layers). Use “folder/name” for subfolders.</p>`,
    buttons: [{ label: 'Cancel', value: false }, { label: 'Save', value: true, kind: 'primary' }],
    onSubmit: (form) => !!(name = form.name.value.trim()),
  });
  if (!ok) return false;
  const file = name.endsWith('.svg') ? name : name + '.svg';
  // Ask about overwriting after the Save As dialog closed (dialogs don't stack); Cancel goes back
  if (existing.has(file) && file !== app.server.file &&
      !await confirmDialog('Replace file?', `“${file}” already exists. Replace it?`, 'Replace', true)) {
    return saveAs(name);
  }
  const s = await api.save(name);
  if (s) toast(`Saved ${s.file}`, 'ok');
  return !!s;
}

export async function importSvg() {
  const file = await pickFile('.svg,image/svg+xml');
  if (!file) return;
  const s = await api.importSvg(await file.text(), { layer: app.activeLayer });
  if (s) toast(`Imported ${s.result} element(s) from ${file.name}`, 'ok');
}

export function exportCnc() {
  const hidden = app.doc.layers.filter(l => l.export && !l.visible).map(l => l.name);
  const skipped = app.doc.layers.filter(l => !l.export).map(l => l.name);
  window.location.href = '/api/export/cnc';
  toast(`CNC export: mm units${skipped.length ? `, without ${skipped.join(', ')}` : ''}${hidden.length ? ` (hidden: ${hidden.join(', ')})` : ''}`);
}

export function downloadSvg() {
  window.location.href = '/api/export/file';
}

export async function exportPng() {
  const c = await docToPng(app.doc);
  c.toBlob(b => download(b, `${app.server.name}.png`), 'image/png');
}

export async function documentSize() {
  let size = null;
  const ok = await modal({
    title: 'Document size',
    html: sizeFields(app.doc.width, app.doc.height),
    buttons: [{ label: 'Cancel', value: false }, { label: 'Apply', value: true, kind: 'primary' }],
    setup: bindPresets,
    onSubmit: (form) => !!(size = readSize(form)),
  });
  if (ok) await api.ops([{ op: 'set_size', width: size.w, height: size.h }]);
}

export async function setBackground() {
  const file = await pickFile('image/*');
  if (!file) return;
  const href = await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(file); });
  await api.ops([{ op: 'set_background', href, opacity: 0.3 }], 'Background image');
}

export const removeBackground = () => api.ops([{ op: 'set_background', href: null }], 'Remove background');

// ── Edit ───────────────────────────────────────────────────

export const undo = () => api.undo();
export const redo = () => api.redo();

export async function deleteSelection() {
  const ids = editableSelection();
  if (!ids.length) return;
  if (await api.ops([{ op: 'remove_elements', ids }], ids.length > 1 ? `Delete ${ids.length} elements` : 'Delete'))
    setSelection([]);
}

export async function duplicate() {
  const els = selectedElements().filter(e => editableSelection().includes(e.id));
  if (!els.length) return;
  const d = app.grid || 10;
  const s = await api.ops(els.map(e => ({ op: 'add_element', tag: e.tag, layer: e.layer, text: e.text,
    attrs: { ...e.attrs, ...moveAttrs(e, d, d) } })), 'Duplicate');
  if (s?.results) setSelection(s.results);
}

export function selectAll() {
  const usable = new Set(app.doc.layers.filter(l => l.visible && !l.locked).map(l => l.name));
  setSelection(app.doc.elements.filter(e => usable.has(e.layer)).map(e => e.id));
}

export async function nudge(dx, dy) {
  const ids = editableSelection();
  if (!ids.length) return;
  await api.ops(ids.map(id => ({ op: 'update_element', id, attrs: moveAttrs(elementById(id), dx, dy), coalesce: 'nudge' })), 'Nudge');
}

export async function reorder(where) {
  const ids = editableSelection();
  const order = where === 'front' ? ids : [...ids].reverse();
  if (ids.length) await api.ops(order.map(id => ({ op: 'reorder_element', id, where })), where === 'front' ? 'Bring to front' : 'Send to back');
}

export async function moveSelectionToLayer(layer) {
  const ids = [...app.selection];
  if (ids.length) await api.ops(ids.map(id => ({ op: 'update_element', id, layer })), `Move to ${layer}`);
}

export async function clearAll() {
  const n = app.doc.elements.length;
  if (!n) return toast('Nothing to clear');
  if (!await confirmDialog('Clear all elements?', `This removes all ${n} elements from every layer. You can undo it (⌘Z).`, 'Clear all', true)) return;
  if (await api.ops([{ op: 'clear' }], 'Clear all')) setSelection([]);
}

function editableSelection() {
  const usable = new Set(app.doc.layers.filter(l => l.visible && !l.locked).map(l => l.name));
  return selectedElements().filter(e => usable.has(e.layer)).map(e => e.id);
}
