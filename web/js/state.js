// Shared client state + a tiny event bus.
// The document itself lives on the server; `app.server` is the last state it sent us.

const listeners = {};

export function on(event, fn) {
  (listeners[event] ||= []).push(fn);
}

export function emit(event, data) {
  (listeners[event] || []).forEach(fn => fn(data));
}

function pref(key, fallback) {
  try {
    const v = localStorage.getItem('svgcnc.' + key);
    return v === null ? fallback : JSON.parse(v);
  } catch (_) { return fallback; }
}

export function savePref(key, value) {
  try { localStorage.setItem('svgcnc.' + key, JSON.stringify(value)); } catch (_) {}
}

export const app = {
  server: null,          // {version, file, name, dirty, can_undo, ..., doc}
  get doc() { return this.server?.doc; },
  selection: new Set(),  // element ids
  activeLayer: null,
  tool: 'select',
  // View preferences (per browser)
  zoom: pref('zoom', null),
  unit: pref('unit', 'cm'),
  snap: pref('snap', true),
  grid: pref('grid', 5),       // mm
  showGrid: pref('showGrid', true),
  codeOpen: false,
  connected: false,
};

export function layerOf(el) {
  return app.doc?.layers.find(l => l.name === el.layer);
}

export function elementById(id) {
  return app.doc?.elements.find(e => e.id === id);
}

export function selectedElements() {
  return (app.doc?.elements || []).filter(e => app.selection.has(e.id));
}

export function setSelection(ids) {
  app.selection = new Set(ids);
  emit('selection');
}

export function isEditable(el) {
  const l = layerOf(el);
  return l && l.visible && !l.locked;
}
