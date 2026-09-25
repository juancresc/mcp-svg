// User actions shared by the menus, toolbar, keyboard shortcuts and panels.

import { app, emit, setSelection, selectedElements, elementById, selectedItems, selectItems, elementsOf,
         groupById, setContext } from './state.js';
import { api } from './api.js';
import { modal, confirmDialog, toast, esc, download, pickFile, fmt } from './ui.js';
import { moveAttrs, docToPng } from './geometry.js';
import * as canvas from './canvas.js';
import { PRESETS as MATERIAL_PRESETS } from './materials.js';

// Files saved to / opened from the user's computer (File System Access API), per tab
const localHandles = new Map();

// ── Unsaved changes ────────────────────────────────────────

async function askSave(name, verb) {
  return modal({
    title: `Save changes to “${name}”?`,
    html: `<p>Your changes will be lost if you ${verb} without saving.</p>`,
    buttons: [
      { label: "Don't save", value: 'discard', kind: 'danger', left: true },
      { label: 'Cancel', value: null },
      { label: 'Save', value: 'save', kind: 'primary' },
    ],
  });
}

// ── Tabs ───────────────────────────────────────────────────

export async function closeTab(tabId = app.server.active) {
  const tab = app.server.tabs.find(t => t.id === tabId);
  if (!tab) return;
  let discard = false;
  if (tab.dirty) {
    if (tabId !== app.server.active) await api.activate(tabId);
    const choice = await askSave(tab.name, 'close it');
    if (choice === null) return;
    if (choice === 'save' && !(await save())) return;
    discard = choice === 'discard';
  }
  app.previewTabs.delete(tabId);
  localHandles.delete(tabId);
  await api.close(tabId, discard);
}

export async function switchTab(tabId, view = '2d') {
  app.view = view;
  if (tabId !== app.server.active) await api.activate(tabId);
  emit('view-mode');
}

export function open3d(tabId = app.server.active) {
  app.previewTabs.add(tabId);
  switchTab(tabId, '3d');
}

export function close3d(tabId) {
  app.previewTabs.delete(tabId);
  if (app.view === '3d' && tabId === app.server.active) app.view = '2d';
  emit('view-mode');
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
  let size = null;
  const ok = await modal({
    title: 'New document',
    html: sizeFields(2440, 1220) + `<div class="kv" style="margin-top:10px"><label>Material</label>
        <select name="material" class="field">${MATERIAL_PRESETS.map((p, i) => `<option value="${i}">${esc(p.name)} mm</option>`).join('')}</select></div>
      <p class="hint" style="margin-top:10px">Opens in a new tab. 1 unit = 1 mm, with the default CNC layers. Material, thickness and sheet size can be changed later in the Inspector.</p>`,
    buttons: [{ label: 'Cancel', value: false }, { label: 'Create', value: true, kind: 'primary' }],
    setup: bindPresets,
    onSubmit: (form) => { size = readSize(form); if (size) size.material = MATERIAL_PRESETS[+form.material.value]; return !!size; },
  });
  if (!ok) return;
  if (await api.newDoc(size.w, size.h)) {
    const p = size.material;
    await api.ops([{ op: 'set_material', material: { name: p.name.replace(/ \d+.*$/, ''), type: p.type, thickness: p.thickness,
      color: p.color, sheet_width: size.w, sheet_height: size.h } }], 'Material');
    afterSwitch();
  }
}

function afterSwitch() {
  setSelection([]);
  setContext(null);
  app.view = '2d';
  emit('view-mode');
  requestAnimationFrame(canvas.zoomFit);
}

/**
 * File browser over the data folder. mode 'open' | 'save'. Resolves with
 * {file} (a data-folder path), {computer: true}, or null.
 */
async function browseDialog(mode, { suggestion = '', folder = '' } = {}) {
  let result = null;
  const state = { folder, selected: null };
  const title = mode === 'open' ? 'Open' : 'Save As';
  const render = async (root) => {
    let listing;
    try { listing = await api.browse(state.folder); } catch (e) { toast(e.message, 'error'); return; }
    state.folder = listing.folder;
    const parts = state.folder ? state.folder.split('/') : [];
    root.querySelector('.fb-crumbs').innerHTML = `<button type="button" data-folder="">data</button>` +
      parts.map((p, i) => `<span>/</span><button type="button" data-folder="${esc(parts.slice(0, i + 1).join('/'))}">${esc(p)}</button>`).join('');
    const rows = [
      ...(state.folder ? [`<button type="button" class="file-item" data-folder="${esc(parts.slice(0, -1).join('/'))}"><span class="ico">↰</span><span>..</span></button>`] : []),
      ...listing.dirs.map(d => `<button type="button" class="file-item" data-folder="${esc(d.path)}"><span class="ico">📁</span><span>${esc(d.name)}</span></button>`),
      ...listing.files.filter(f => mode === 'open' || (f.kind === 'kerf' || f.kind === 'svgcnc')).map(f => `<button type="button" class="file-item" data-file="${esc(f.file)}"><span class="ico" title="${f.kind}">${{ kerf: '📐', svgcnc: '📐', svg: '🖼', dxf: '📏' }[f.kind] || '📄'}</span><span>${esc(f.name)}</span>
        ${app.server.tabs.some(t => t.file === f.file) ? '<span class="current">open</span>' : ''}
        <span class="meta">${new Date(f.modified * 1000).toLocaleString()} · ${fmt(f.size / 1024, 1)} KB</span></button>`),
    ];
    root.querySelector('.file-list').innerHTML = rows.join('') || '<p class="muted" style="padding:10px">Empty folder</p>';
  };
  const choice = await modal({
    title,
    html: `<div class="fb-bar"><div class="fb-crumbs"></div>
        ${mode === 'save' ? '<button type="button" class="btn" data-mkdir>New folder</button>' : ''}</div>
      <div class="file-list"></div>
      ${mode === 'save' ? `<div class="fb-name"><label>File name</label><input name="name" class="field" value="${esc(suggestion)}"></div>` : ''}
      <p class="hint" style="margin-top:8px">${mode === 'save'
        ? 'Saved as a Kerf project (.kerf): layers, entities, 3D placements, material. SVG/DXF are in Export. “Save to computer…” saves anywhere on your machine.'
        : '📐 projects (.kerf) · 🖼 SVG and 📏 DXF open as new documents. “Open from computer…” opens files from anywhere on your machine.'}</p>`,
    buttons: [{ label: mode === 'save' ? 'Save to computer…' : 'Open from computer…', value: 'computer', left: true },
      { label: 'Cancel', value: null }, { label: mode === 'save' ? 'Save' : 'Open', value: 'ok', kind: 'primary' }],
    setup: (form, close) => {
      form.closest('dialog').classList.add('wide');
      render(form);
      form.addEventListener('click', async (e) => {
        const fbtn = e.target.closest('[data-folder]');
        if (fbtn) { state.folder = fbtn.dataset.folder; state.selected = null; await render(form); return; }
        const file = e.target.closest('[data-file]');
        if (file) {
          form.querySelectorAll('.file-item').forEach(x => x.classList.remove('selected'));
          file.classList.add('selected');
          state.selected = file.dataset.file;
          if (mode === 'save') form.name.value = file.dataset.file.split('/').pop().replace(/\.(kerf|svgcnc|svg|dxf)$/, '');
        }
        if (e.target.closest('[data-mkdir]')) {
          const name = prompt('New folder name');
          if (name && name.trim()) {
            const f = [state.folder, name.trim()].filter(Boolean).join('/');
            if (await api.mkdir(f)) { state.folder = f; await render(form); }
          }
        }
      });
      form.addEventListener('dblclick', (e) => {
        const file = e.target.closest('[data-file]');
        if (file && mode === 'open') { state.selected = file.dataset.file; close('ok'); }
      });
    },
    onSubmit: (form) => {
      if (mode === 'open') { if (!state.selected) { toast('Pick a file'); return false; } return; }
      const name = form.name.value.trim();
      if (!name) return false;
      result = [state.folder, name].filter(Boolean).join('/');
    },
  });
  if (choice === 'computer') return { computer: true };
  if (choice !== 'ok') return null;
  return { file: mode === 'open' ? state.selected : result };
}

export async function openDocument() {
  const r = await browseDialog('open');
  if (!r) return;
  if (r.computer) return openFromComputer();
  if (await api.open(r.file)) { afterSwitch(); toast(`Opened ${r.file}`, 'ok'); }
}

export async function openFromComputer() {
  let file, handle = null;
  if (window.showOpenFilePicker) {
    try {
      [handle] = await window.showOpenFilePicker({ types: [{ description: 'Project, SVG or DXF', accept: {
        'application/json': ['.kerf', '.svgcnc'], 'image/svg+xml': ['.svg'], 'application/dxf': ['.dxf'] } }] });
      file = await handle.getFile();
    } catch (_) { return; }   // cancelled
  } else {
    file = await pickFile('.kerf,.svgcnc,.svg,.dxf,image/svg+xml');
    if (!file) return;
  }
  const name = file.name.replace(/\.(kerf|svgcnc|svg|dxf)$/i, '');
  const kind = (file.name.match(/\.(kerf|svgcnc|svg|dxf)$/i) || [, 'svg'])[1].toLowerCase().replace('svgcnc', 'kerf');
  const isDxf = kind === 'dxf';
  const s = kind === 'kerf' ? await api.importProject(await file.text(), { name })
    : isDxf ? await api.importDxf(await toBase64(file), { new_tab: true, name })
    : await api.importSvg(await file.text(), { new_tab: true, name });
  if (!s) return;
  if (handle && kind === 'kerf') {   // a project opened from disk can be saved back in place
    localHandles.set(s.active, handle);
    await api.savedLocal(file.name);
  }
  afterSwitch();
  toast(`Opened ${file.name}${isDxf ? ' (converted from DXF)' : ''}`, 'ok');
}

function toBase64(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result).split(',')[1]);
    fr.onerror = reject;
    fr.readAsDataURL(file);
  });
}

export async function save() {
  const tab = app.server.tabs.find(t => t.id === app.server.active);
  if (app.server.file) {
    const s = await api.save();
    if (s) toast(`Saved ${s.file}`, 'ok');
    return !!s;
  }
  const handle = localHandles.get(app.server.active);
  if (handle) return writeToHandle(handle);
  return saveAs(tab?.name);
}

export async function saveAs(initial) {
  const suggestion = initial || (app.server.file ? app.server.file.split('/').pop().replace(/\.(kerf|svgcnc)$/, '') : app.server.name || 'untitled');
  const folder = app.server.file && app.server.file.includes('/') ? app.server.file.split('/').slice(0, -1).join('/') : '';
  const r = await browseDialog('save', { suggestion, folder });
  if (!r) return false;
  if (r.computer) return saveToComputer();
  const file = r.file.replace(/\.(kerf|svgcnc|svg)$/, '') + '.kerf';
  let exists = false;
  try { exists = (await api.files()).files.some(f => f.file === file); } catch (_) {}
  if (exists && file !== app.server.file &&
      !await confirmDialog('Replace file?', `“${file}” already exists. Replace it?`, 'Replace', true)) {
    return saveAs(r.file.split('/').pop());
  }
  const s = await api.save(r.file);
  if (s) { localHandles.delete(app.server.active); toast(`Saved ${s.file}`, 'ok'); }
  return !!s;
}

async function writeToHandle(handle) {
  try {
    const svg = await api.exportText('project');
    const w = await handle.createWritable();
    await w.write(svg);
    await w.close();
    await api.savedLocal(handle.name);
    toast(`Saved ${handle.name} on your computer`, 'ok');
    return true;
  } catch (e) {
    toast(`Could not save: ${e.message}`, 'error');
    return false;
  }
}

export async function saveToComputer() {
  const name = `${app.server.name || 'untitled'}.kerf`;
  if (window.showSaveFilePicker) {
    let handle;
    try {
      handle = await window.showSaveFilePicker({ suggestedName: name,
        types: [{ description: 'Kerf project', accept: { 'application/json': ['.kerf'] } }] });
    } catch (_) { return false; }  // cancelled
    localHandles.set(app.server.active, handle);
    return writeToHandle(handle);
  }
  const svg = await api.exportText('project');
  download(new Blob([svg], { type: 'application/json' }), name);
  await api.savedLocal(name);
  toast('Downloaded — your browser saves it to its downloads folder');
  return true;
}

export async function revert() {
  if (!app.server.file) return toast('This document has never been saved to the data folder', 'error');
  if (!await confirmDialog('Revert to saved?', `Reload “${app.server.file}” from disk? Your unsaved changes can still be undone afterwards.`, 'Revert')) return;
  await api.revert();
}

// ── Import ─────────────────────────────────────────────────

export async function importSvg() {
  const file = await pickFile('.svg,image/svg+xml');
  if (!file) return;
  const s = await api.importSvg(await file.text(), { layer: app.activeLayer });
  if (s) toast(`Imported ${s.result} element(s) from ${file.name}`, 'ok');
}

export async function importDxf(intoDocument = false) {
  const file = await pickFile('.dxf');
  if (!file) return;
  const name = file.name.replace(/\.dxf$/i, '');
  const s = await api.importDxf(await toBase64(file), { new_tab: !intoDocument, name, layer: app.activeLayer });
  if (!s) return;
  if (!intoDocument) afterSwitch();
  toast(`Imported ${s.result} element(s) from ${file.name}`, 'ok');
}

// ── Export ─────────────────────────────────────────────────

function exportNote() {
  const hidden = app.doc.layers.filter(l => l.export && !l.visible).map(l => l.name);
  const skipped = app.doc.layers.filter(l => !l.export).map(l => l.name);
  return `mm units${skipped.length ? `, without ${skipped.join(', ')}` : ''}${hidden.length ? ` (hidden: ${hidden.join(', ')})` : ''}`;
}

export function exportCnc() {
  window.location.href = '/api/export/cnc';
  toast(`CNC SVG: ${exportNote()}`);
}

export function exportDxf() {
  window.location.href = '/api/export/cnc-dxf';
  toast(`CNC DXF: ${exportNote()}`);
}

export async function exportParts() {
  const tops = (app.doc.groups || []).filter(g => !g.parent);
  if (!tops.length) {
    return modal({ title: 'No entities yet', html: '<p>Part files are made per entity. Select the shapes of one part (outline + holes) and press <b>⌘G</b> to group them, set the quantity in the inspector, then export again.</p>' });
  }
  window.location.href = '/api/export/parts';
  toast(`Parts: ${tops.map(g => `${g.name} ×${g.qty}`).join(', ')} — SVG + DXF each`);
}

export function downloadSvg() {
  window.location.href = '/api/export/file';
}

export async function export3d(format) {
  const { exportModel } = await import('./preview3d.js');
  const ok = await exportModel(format);
  if (ok) toast(`3D model exported as ${format.toUpperCase()}`, 'ok');
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

function usableLayers() {
  return new Set(app.doc.layers.filter(l => l.visible && !l.locked).map(l => l.name));
}

function editableSelection() {
  const usable = usableLayers();
  return selectedElements().filter(e => usable.has(e.layer)).map(e => e.id);
}

export async function deleteSelection() {
  const ids = editableSelection();
  if (!ids.length) return;
  if (await api.ops([{ op: 'remove_elements', ids }], ids.length > 1 ? `Delete ${ids.length} elements` : 'Delete'))
    setSelection([]);
}

/** Duplicate the selection; a selected entity is copied as a new entity (one level). */
export async function duplicate() {
  const d = app.grid || 10;
  const usable = usableLayers();
  const items = selectedItems();
  if (!items.length) return;
  const ops = [];
  const plan = [];                   // per item: [op indexes] for regrouping
  for (const it of items) {
    const idx = [];
    for (const id of elementsOf(it)) {
      const e = elementById(id);
      if (!usable.has(e.layer)) continue;
      idx.push(ops.length);
      ops.push({ op: 'add_element', tag: e.tag, layer: e.layer, text: e.text, group: app.context || undefined,
                 attrs: { ...e.attrs, ...moveAttrs(e, d, d) } });
    }
    plan.push({ it, idx });
  }
  if (!ops.length) return;
  const s = await api.ops(ops, 'Duplicate');
  if (!s?.results) return;
  const newIds = s.results;
  const groupOps = plan.filter(p => p.it.startsWith('g-') && p.idx.length)
    .map(p => ({ op: 'group', items: p.idx.map(i => newIds[i]), name: `${groupById(p.it)?.name || 'Entity'} copy` }));
  let finalIds = newIds;
  if (groupOps.length) {
    const g = await api.ops(groupOps, 'Duplicate');
    if (g?.results) return selectItems([...g.results, ...plan.filter(p => !p.it.startsWith('g-')).flatMap(p => p.idx.map(i => newIds[i]))]);
  }
  setSelection(finalIds);
}

export function selectAll() {
  const usable = usableLayers();
  const inScope = app.context ? new Set(elementsOf(app.context)) : null;
  setSelection(app.doc.elements.filter(e => usable.has(e.layer) && (!inScope || inScope.has(e.id))).map(e => e.id));
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
  if (!await confirmDialog('Clear all elements?', `This removes all ${n} elements and entities from every layer. You can undo it (⌘Z).`, 'Clear all', true)) return;
  if (await api.ops([{ op: 'clear' }], 'Clear all')) { setSelection([]); setContext(null); }
}

// ── Entities (groups) ──────────────────────────────────────

export async function group() {
  const items = selectedItems();
  if (!items.length) return toast('Select the shapes of one part first');
  if (items.length === 1 && items[0].startsWith('g-')) return toast('That is already one entity');
  let name = null;
  const ok = await modal({
    title: 'Group into entity',
    html: `<div class="kv"><label>Name</label><input name="name" class="field" value="Part ${(app.doc.groups || []).length + 1}"></div>
      <p class="hint" style="margin-top:8px">An entity is one part (e.g. outline + holes, even on different layers). Entities are exported as separate part files and placed in the 3D preview.</p>`,
    buttons: [{ label: 'Cancel', value: false }, { label: 'Group', value: true, kind: 'primary' }],
    onSubmit: (form) => !!(name = form.name.value.trim()),
  });
  if (!ok) return;
  const s = await api.ops([{ op: 'group', items, name, parent: app.context }], 'Group');
  if (s?.results) selectItems([s.results[0]]);
}

export async function ungroup() {
  const items = selectedItems().filter(it => it.startsWith('g-'));
  if (!items.length) return toast('Select an entity to ungroup');
  const els = items.flatMap(elementsOf);
  await api.ops(items.map(id => ({ op: 'ungroup', id })), 'Ungroup');
  setSelection(els);
}

export function enterGroup() {
  const it = selectedItems().find(i => i.startsWith('g-'));
  if (it) canvas.enterGroup(it);
  else toast('Select an entity, then Enter (or double-click it)');
}

// ── Measure → dimension on NOTES ───────────────────────────

export async function addDimension() {
  const m = canvas.currentMeasure();
  if (!m) return toast('Measure something first (M, then drag)');
  const layer = app.doc.layers.find(l => l.name === 'NOTES') || app.doc.layers.find(l => !l.export) || app.doc.layers[0];
  const { a, b } = m;
  const len = Math.hypot(b.x - a.x, b.y - a.y);
  const ux = (b.x - a.x) / len, uy = (b.y - a.y) / len, nx = -uy, ny = ux;
  const t = 4, size = Math.max(4, Math.min(20, len / 12));
  const r = v => Math.round(v * 1000) / 1000;
  const ops = [
    { op: 'add_element', tag: 'line', layer: layer.name, attrs: { x1: r(a.x), y1: r(a.y), x2: r(b.x), y2: r(b.y) } },
    { op: 'add_element', tag: 'line', layer: layer.name, attrs: { x1: r(a.x - nx * t), y1: r(a.y - ny * t), x2: r(a.x + nx * t), y2: r(a.y + ny * t) } },
    { op: 'add_element', tag: 'line', layer: layer.name, attrs: { x1: r(b.x - nx * t), y1: r(b.y - ny * t), x2: r(b.x + nx * t), y2: r(b.y + ny * t) } },
    { op: 'add_element', tag: 'text', layer: layer.name, text: fmt(len),
      attrs: { x: r((a.x + b.x) / 2 - nx * (size * 0.6)), y: r((a.y + b.y) / 2 - ny * (size * 0.6)), 'font-size': r(size),
               'font-family': 'sans-serif', 'text-anchor': 'middle',
               ...(Math.abs(Math.atan2(uy, ux)) > 1e-3 ? { transform: `rotate(${r(normAngle(Math.atan2(uy, ux) * 180 / Math.PI))} ${r((a.x + b.x) / 2)} ${r((a.y + b.y) / 2)})` } : {}) } },
  ];
  const s = await api.ops(ops, 'Add dimension');
  if (!s?.results) return;
  await api.ops([{ op: 'group', items: s.results, name: `Dimension ${fmt(len)}` }], 'Add dimension');
  canvas.clearMeasure();
  toast(`Dimension ${fmt(len)} mm added to ${layer.name}`, 'ok');
}

const normAngle = a => (a > 90 ? a - 180 : a < -90 ? a + 180 : a);
