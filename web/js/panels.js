// Right side: Layers panel and the Inspector (selected shape / document).

import { app, on, emit, setSelection, selectedElements, layerOf } from './state.js';
import { api } from './api.js';
import { icons, esc, fmt, modal, toast, confirmDialog } from './ui.js';
import { docToSvg, screenDashOf } from './geometry.js';
import * as canvas from './canvas.js';
import * as actions from './actions.js';

const layersBox = document.getElementById('layers');
const inspector = document.getElementById('inspector');
const expanded = new Set();     // layer names with details open

// ── Layers ─────────────────────────────────────────────────

let layersQueued = false;
export function renderLayers() {
  const doc = app.doc;
  if (!doc) return;
  const active = document.activeElement;
  if (layersBox.contains(active) && active.matches('input[type=text], input:not([type]), textarea')) {
    layersQueued = true;   // re-render after the rename/description edit ends
    return;
  }
  layersQueued = false;
  if (!doc.layers.some(l => l.name === app.activeLayer)) app.activeLayer = doc.layers[0]?.name;
  const counts = {};
  doc.elements.forEach(e => counts[e.layer] = (counts[e.layer] || 0) + 1);

  // Top of the list = drawn on top (last in document order)
  layersBox.innerHTML = [...doc.layers].reverse().map(l => {
    const i = doc.layers.indexOf(l);
    const open = expanded.has(l.name);
    return `<div class="layer ${l.name === app.activeLayer ? 'active' : ''} ${l.visible ? '' : 'hidden'}" data-layer="${esc(l.name)}" title="${esc(l.description)}">
      <div class="layer-row">
        <button class="icon-btn" data-act="expand" title="Details">${open ? icons.chevronDown : icons.chevron}</button>
        <button class="icon-btn ${l.visible ? '' : 'off'}" data-act="visible" title="${l.visible ? 'Hide' : 'Show'}">${l.visible ? icons.eye : icons.eyeOff}</button>
        <button class="icon-btn ${l.locked ? '' : 'off'}" data-act="lock" title="${l.locked ? 'Unlock' : 'Lock (not selectable)'}">${l.locked ? icons.lock : icons.unlock}</button>
        <label class="swatch" style="background:${l.color}" title="Colour"><input type="color" value="${l.color}" data-act="color"></label>
        <span class="layer-name" data-act="name" title="Double-click to rename">${esc(l.name)}</span>
        ${l.export ? '' : '<span class="no-export" title="Not included in CNC export">no cut</span>'}
        <svg class="style-mini" viewBox="0 0 22 10"><line x1="1" y1="5" x2="21" y2="5" stroke="${l.color}" stroke-width="2" stroke-dasharray="${screenDashOf(l) || 'none'}" stroke-linecap="round"/></svg>
        <span class="layer-count">${counts[l.name] || 0}</span>
      </div>
      ${open ? `<div class="layer-details">
        <textarea data-act="description" placeholder="What is this layer for? (e.g. pocket 6 mm deep)">${esc(l.description)}</textarea>
        <div class="row"><span class="grow">Line style</span>
          <select class="field" style="width:auto" data-act="style">
            ${['solid', 'dashed', 'dotted'].map(s => `<option ${l.line_style === s ? 'selected' : ''}>${s}</option>`).join('')}
            ${['solid', 'dashed', 'dotted'].includes(l.line_style) ? '' : `<option selected>${esc(l.line_style)}</option>`}
          </select></div>
        <label class="row"><input type="checkbox" data-act="export" ${l.export ? 'checked' : ''}> <span class="grow">Include in CNC export</span></label>
        <div class="row">
          <button class="icon-btn" data-act="up" title="Move up (draw on top)" ${i === doc.layers.length - 1 ? 'disabled' : ''}>${icons.up}</button>
          <button class="icon-btn" data-act="down" title="Move down" ${i === 0 ? 'disabled' : ''}>${icons.down}</button>
          <span class="grow"></span>
          <button class="btn danger" data-act="delete">Delete layer</button>
        </div>
      </div>` : ''}
    </div>`;
  }).join('');
  emit('active-layer');
}

const layerOp = (name, fields, label) => api.ops([{ op: 'update_layer', name, ...fields }], label);

layersBox.addEventListener('click', async (e) => {
  const row = e.target.closest('.layer');
  if (!row) return;
  const name = row.dataset.layer;
  const layer = app.doc.layers.find(l => l.name === name);
  const act = e.target.closest('[data-act]')?.dataset.act;
  switch (act) {
    case 'expand':
      expanded.has(name) ? expanded.delete(name) : expanded.add(name);
      return renderLayers();
    case 'visible':
      return api.ops([{ op: 'set_layer_visibility', name, visible: !layer.visible }]);
    case 'lock':
      return layerOp(name, { locked: !layer.locked }, layer.locked ? 'Unlock layer' : 'Lock layer');
    case 'up':
    case 'down': {
      const i = app.doc.layers.indexOf(layer);
      return api.ops([{ op: 'move_layer', name, index: act === 'up' ? i + 1 : i - 1 }]);
    }
    case 'delete':
      return deleteLayer(layer);
    case 'color': case 'description': case 'style': case 'export': case 'name':
      if (act !== 'name') return;
  }
  if (!e.target.closest('.layer-details, input, select, textarea')) {
    app.activeLayer = name;
    renderLayers();
  }
});

layersBox.addEventListener('dblclick', (e) => {
  const span = e.target.closest('[data-act=name]');
  if (!span) return;
  const name = span.closest('.layer').dataset.layer;
  span.innerHTML = `<input value="${esc(name)}">`;
  const input = span.querySelector('input');
  input.focus(); input.select();
  let done = false;
  const commit = async (ok) => {
    if (done) return;
    done = true;
    const v = input.value.trim();
    if (ok && v && v !== name) {
      if (await layerOp(name, { new_name: v }, 'Rename layer')) {
        if (app.activeLayer === name) app.activeLayer = v;
        if (expanded.delete(name)) expanded.add(v);
      }
    }
    renderLayers();
  };
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') commit(true);
    if (ev.key === 'Escape') commit(false);
    ev.stopPropagation();
  });
  input.addEventListener('blur', () => commit(true));
});

layersBox.addEventListener('change', (e) => {
  const act = e.target.dataset.act;
  const name = e.target.closest('.layer')?.dataset.layer;
  if (!name) return;
  if (act === 'color') layerOp(name, { color: e.target.value }, 'Layer colour');
  if (act === 'style') layerOp(name, { line_style: e.target.value }, 'Line style');
  if (act === 'export') layerOp(name, { export: e.target.checked }, 'Layer export');
  if (act === 'description') layerOp(name, { description: e.target.value }, 'Layer description');
});
layersBox.addEventListener('focusout', () => setTimeout(() => { if (layersQueued) renderLayers(); }, 0));

// Live colour preview while dragging in the picker
layersBox.addEventListener('input', (e) => {
  if (e.target.dataset.act === 'color') e.target.parentElement.style.background = e.target.value;
});

async function deleteLayer(layer) {
  const doc = app.doc;
  if (doc.layers.length === 1) return toast('A document needs at least one layer', 'error');
  const n = doc.elements.filter(e => e.layer === layer.name).length;
  if (!n) {
    if (await confirmDialog('Delete layer?', `Delete the empty layer “${layer.name}”?`, 'Delete', true))
      api.ops([{ op: 'remove_layer', name: layer.name }], 'Delete layer');
    return;
  }
  const others = doc.layers.filter(l => l.name !== layer.name);
  let target = null;
  const ok = await modal({
    title: `Delete layer “${layer.name}”?`,
    html: `<p>It contains ${n} element(s). What should happen to them?</p>
      <div class="kv"><label>Elements</label><select name="target" class="field">
        ${others.map(l => `<option value="${esc(l.name)}">Move to ${esc(l.name)}</option>`).join('')}
        <option value="__delete__">Delete them</option></select></div>`,
    buttons: [{ label: 'Cancel', value: false }, { label: 'Delete layer', value: true, kind: 'primary danger-solid' }],
    onSubmit: (form) => { target = form.target.value; },
  });
  if (ok) api.ops([{ op: 'remove_layer', name: layer.name, move_to: target }], 'Delete layer');
}

export async function addLayer() {
  let v = null;
  const n = app.doc.layers.length + 1;
  const ok = await modal({
    title: 'New layer',
    html: `<div class="kv">
      <label>Name</label><input name="name" class="field" value="LAYER_${n}" required>
      <label>Colour</label><input name="color" type="color" value="#8e44ad" style="width:60px;height:28px">
      <label>Line style</label><select name="line_style" class="field"><option>solid</option><option>dashed</option><option>dotted</option></select>
      <label>CNC export</label><label><input type="checkbox" name="export" checked> include when exporting</label>
      <label>Description</label><textarea name="description" class="field" placeholder="e.g. Pocket, 6 mm deep, 6 mm end mill"></textarea>
    </div>`,
    buttons: [{ label: 'Cancel', value: false }, { label: 'Create', value: true, kind: 'primary' }],
    onSubmit: (form) => {
      v = { name: form.name.value.trim(), color: form.color.value, line_style: form.line_style.value,
            export: form.export.checked, description: form.description.value.trim() };
      return !!v.name;
    },
  });
  if (!ok) return;
  if (await api.ops([{ op: 'add_layer', ...v }], 'Add layer')) {
    app.activeLayer = v.name;
    renderLayers();
  }
}

// ── Inspector ──────────────────────────────────────────────

const GEOMETRY = {
  line: [['x1', 'y1'], ['x2', 'y2']],
  rect: [['x', 'y'], ['width', 'height'], ['rx']],
  circle: [['cx', 'cy'], ['r', 'diameter']],
  ellipse: [['cx', 'cy'], ['rx', 'ry']],
  text: [['x', 'y'], ['font-size']],
  path: [['d']],
  polygon: [['points']],
  polyline: [['points']],
};
const LABELS = { diameter: '⌀', 'font-size': 'size' };

export function renderInspector() {
  const doc = app.doc;
  if (!doc) return;
  const sel = selectedElements();
  if (!sel.length) return renderDocumentInfo();
  const box = canvas.selectionBox();
  const preview = previewSvg(sel, box);
  const size = box ? `${fmt(box.width)} × ${fmt(box.height)} mm` : '—';

  if (sel.length > 1) {
    const layers = new Set(sel.map(e => e.layer));
    inspector.innerHTML = `<h2>${sel.length} selected</h2><div class="preview" style="margin-top:8px">${preview}</div>
      <div class="kv">
        <label>Size</label><span class="val">${size}</span>
        <label>Position</label><span class="val">${box ? `${fmt(box.x)}, ${fmt(box.y)}` : '—'}</span>
        <label>Layer</label>${layerSelect(layers.size === 1 ? [...layers][0] : '')}
      </div>
      ${buttons()}`;
  } else {
    const el = sel[0];
    const rows = (GEOMETRY[el.tag] || []).map(group => {
      if (group[0] === 'd' || group[0] === 'points')
        return `<label>${group[0]}</label><textarea data-attr="${group[0]}" spellcheck="false">${esc(el.attrs[group[0]] || '')}</textarea>`;
      const inputs = group.map(a => {
        const v = a === 'diameter' ? 2 * (+el.attrs.r || 0) : el.attrs[a];
        return `<span class="unit" data-unit="${a === 'font-size' ? 'mm' : 'mm'}" title="${a}"><input type="number" step="any" data-attr="${a}" value="${fmt(v, 3)}"></span>`;
      }).join('');
      return `<label>${group.map(a => LABELS[a] || a).join(' / ')}</label><div class="${group.length > 1 ? 'pair' : ''}">${inputs}</div>`;
    }).join('');
    const hasFill = el.tag !== 'line' && el.tag !== 'text' && el.tag !== 'polyline';
    const fill = el.attrs.fill && el.attrs.fill !== 'none' ? el.attrs.fill : '';
    inspector.innerHTML = `<h2>Selected shape <span class="tag-pill">${el.tag}</span> <span class="tag-pill">${el.id}</span></h2>
      <div class="preview" style="margin-top:8px">${preview}</div>
      <div class="kv">
        <label>Layer</label>${layerSelect(el.layer)}
        <label>Bounds</label><span class="val">${size}</span>
        ${rows}
        ${el.tag === 'text' ? `<label>text</label><input type="text" data-text value="${esc(el.text)}">` : ''}
        ${hasFill ? `<label>fill</label><div class="fill-row">
            <label><input type="checkbox" data-fill-on ${fill ? 'checked' : ''}> filled</label>
            <input type="color" data-fill value="${fill && fill.startsWith('#') && fill.length === 7 ? fill : '#cccccc'}" ${fill ? '' : 'disabled'}></div>` : ''}
        ${el.attrs.transform ? `<label>transform</label><input type="text" data-attr="transform" value="${esc(el.attrs.transform)}">` : ''}
      </div>
      <p class="hint" style="margin-top:8px">Stroke colour and line style come from the layer.</p>
      ${buttons()}`;
    bindShapeFields(el);
  }
  inspector.querySelector('[data-layer-select]')?.addEventListener('change', (e) => {
    if (e.target.value) actions.moveSelectionToLayer(e.target.value);
  });
  inspector.querySelectorAll('[data-action]').forEach(b => b.addEventListener('click', () => actions[b.dataset.action]()));
  inspector.querySelector('[data-front]')?.addEventListener('click', () => actions.reorder('front'));
  inspector.querySelector('[data-back]')?.addEventListener('click', () => actions.reorder('back'));
  inspector.querySelector('[data-zoom]')?.addEventListener('click', () => canvas.zoomToSelection());
}

function layerSelect(current) {
  return `<select data-layer-select>${current ? '' : '<option value="">(mixed)</option>'}${app.doc.layers.map(l =>
    `<option ${l.name === current ? 'selected' : ''}>${esc(l.name)}</option>`).join('')}</select>`;
}

function buttons() {
  return `<div class="btn-row">
    <button class="btn" data-action="duplicate">Duplicate</button>
    <button class="btn" data-zoom>Zoom to</button>
    <button class="btn" data-front>Bring to front</button>
    <button class="btn" data-back>Send to back</button>
    <button class="btn danger" data-action="deleteSelection" style="grid-column: span 2">Delete</button>
  </div>`;
}

function previewSvg(els, box) {
  if (!box) return '';
  const pad = Math.max(box.width, box.height) * 0.08 + 1;
  return docToSvg(app.doc, { background: false, ids: new Set(els.map(e => e.id)),
    viewBox: [box.x - pad, box.y - pad, box.width + 2 * pad, box.height + 2 * pad] })
    .replace('<svg ', '<svg preserveAspectRatio="xMidYMid meet" ');
}

function bindShapeFields(el) {
  inspector.querySelectorAll('[data-attr]').forEach(input => {
    input.addEventListener('input', () => {
      const a = input.dataset.attr;
      let v = input.value.trim();
      if (input.type === 'number' && (v === '' || isNaN(+v))) return;
      const attrs = a === 'diameter' ? { r: +v / 2 } : { [a]: v };
      api.ops([{ op: 'update_element', id: el.id, attrs }], `Edit ${a}`);
    });
  });
  inspector.querySelector('[data-text]')?.addEventListener('input', (e) =>
    api.ops([{ op: 'update_element', id: el.id, text: e.target.value }], 'Edit text'));
  const fillOn = inspector.querySelector('[data-fill-on]'), fillColor = inspector.querySelector('[data-fill]');
  fillOn?.addEventListener('change', () => {
    fillColor.disabled = !fillOn.checked;
    api.ops([{ op: 'update_element', id: el.id, attrs: { fill: fillOn.checked ? fillColor.value : 'none' } }], 'Fill');
  });
  fillColor?.addEventListener('change', () => api.ops([{ op: 'update_element', id: el.id, attrs: { fill: fillColor.value } }], 'Fill'));
}

function renderDocumentInfo() {
  const doc = app.doc, s = app.server;
  const counts = doc.layers.map(l => [l, doc.elements.filter(e => e.layer === l.name).length]);
  const active = doc.layers.find(l => l.name === app.activeLayer);
  inspector.innerHTML = `<h2>Document</h2>
    <div class="kv" style="margin-top:8px">
      <label>File</label><span class="val">${s.file ? esc(s.file) : '<span class="muted">not saved yet</span>'}${s.dirty ? ' •' : ''}</span>
      <label>Size</label><span class="val">${fmt(doc.width)} × ${fmt(doc.height)} mm</span>
      <label>Elements</label><span class="val">${doc.elements.length}</span>
    </div>
    <div class="btn-row"><button class="btn" data-doc-size>Change size…</button><button class="btn" data-fit>Fit to window</button></div>
    ${active ? `<h3>Active layer: ${esc(active.name)}</h3><p class="hint">${esc(active.description) || 'No description. Open the layer details (▸) to add one.'}</p>` : ''}
    <h3>CNC export</h3>
    <p class="hint">${counts.filter(([l]) => l.export && l.visible).map(([l, n]) => `${esc(l.name)} (${n})`).join(', ') || 'nothing'} will be exported.
      ${counts.filter(([l]) => !l.export).length ? `Not exported: ${counts.filter(([l]) => !l.export).map(([l]) => esc(l.name)).join(', ')}.` : ''}</p>
    <h3>Reference image</h3>
    ${doc.background ? `<div class="kv"><label>Opacity</label><input type="range" min="0" max="1" step="0.05" value="${doc.background.opacity}" data-bg-opacity></div>
      <div class="btn-row"><button class="btn" data-bg-set>Replace…</button><button class="btn danger" data-bg-remove>Remove</button></div>`
      : `<p class="hint">Trace over a photo or drawing. Saved with the document, never exported.</p><div class="btn-row"><button class="btn" data-bg-set>Set image…</button></div>`}
    <h3>Tips</h3>
    <p class="hint">Click a shape to select it. Drag on empty space to box-select: left→right selects shapes fully inside, right→left selects anything touched. Shift adds. Arrows nudge (Shift ×10). Space-drag pans, ⌘-scroll zooms.</p>`;
  inspector.querySelector('[data-doc-size]').addEventListener('click', actions.documentSize);
  inspector.querySelector('[data-fit]').addEventListener('click', canvas.zoomFit);
  inspector.querySelector('[data-bg-set]').addEventListener('click', actions.setBackground);
  inspector.querySelector('[data-bg-remove]')?.addEventListener('click', actions.removeBackground);
  inspector.querySelector('[data-bg-opacity]')?.addEventListener('change', (e) =>
    api.ops([{ op: 'set_background_opacity', opacity: +e.target.value }], 'Background opacity'));
}

// Re-render the inspector when state changes, but never while the user is typing in it
let inspectorQueued = false;
export function refreshInspector() {
  if (inspector.contains(document.activeElement) && document.activeElement.matches('input:not([type=checkbox]):not([type=range]):not([type=color]), textarea')) {
    inspectorQueued = true;
    return;
  }
  inspectorQueued = false;
  renderInspector();
}
inspector.addEventListener('focusout', () => setTimeout(() => { if (inspectorQueued) refreshInspector(); }, 0));

document.getElementById('layer-add').innerHTML = icons.plus;
document.getElementById('layer-add').addEventListener('click', addLayer);
on('selection', refreshInspector);
