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
    const v = localStorage.getItem('kerf.' + key);
    return v === null ? fallback : JSON.parse(v);
  } catch (_) { return fallback; }
}

export function savePref(key, value) {
  try { localStorage.setItem('kerf.' + key, JSON.stringify(value)); } catch (_) {}
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

// Lookup indexes, rebuilt lazily once per document version
let index = { key: null, els: new Map(), groups: new Map(), desc: new Map() };
function idx() {
  const key = app.server ? `${app.server.active}:${app.server.version}` : null;
  if (index.key !== key) {
    index = { key, els: new Map((app.doc?.elements || []).map(e => [e.id, e])),
              groups: new Map((app.doc?.groups || []).map(g => [g.id, g])), desc: new Map() };
  }
  return index;
}

export function elementById(id) {
  return idx().els.get(id);
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

// ── Entities (groups) ──────────────────────────────────────
// app.context: the group the user has "entered" (drill-down), or null for the top level.
// Clicking selects the *item* at the current level: a child group (all its elements) or a
// loose element.

app.context = null;
app.view = '2d';               // '2d' drawing or '3d' preview of the active document
app.showDims = pref('showDims', true);

export function groupById(id) {
  return idx().groups.get(id);
}

export function parentOf(item) {
  return item.startsWith('g-') ? groupById(item)?.parent ?? null : elementById(item)?.group ?? null;
}

export function descendants(gid) {
  const cache = idx().desc;
  if (cache.has(gid)) return cache.get(gid);
  const out = descendantsUncached(gid);
  cache.set(gid, out);
  return out;
}

function descendantsUncached(gid) {
  const groups = app.doc?.groups || [];
  const kids = new Set([gid]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const g of groups) if (kids.has(g.parent) && !kids.has(g.id)) { kids.add(g.id); grew = true; }
  }
  return (app.doc?.elements || []).filter(e => kids.has(e.group)).map(e => e.id);
}

export function ancestors(id) {
  const out = [];
  let g = id?.startsWith('g-') ? id : elementById(id)?.group;
  while (g) { out.push(g); g = groupById(g)?.parent ?? null; }
  return out;     // innermost first
}

/** The item (element or group id) that represents element `eid` at the current level,
 *  or null if the element is outside the entered group. */
export function itemAt(eid, context = app.context) {
  const chain = [eid, ...ancestors(eid)];           // element, its group, …, top group
  const i = chain.findIndex(id => parentOf(id) === context);
  return i === -1 ? null : chain[i];
}

export function elementsOf(item) {
  return item.startsWith('g-') ? descendants(item) : [item];
}

/** Items at the current level that are fully selected. */
export function selectedItems() {
  const items = new Set();
  for (const id of app.selection) {
    const it = itemAt(id);
    if (it) items.add(it);
  }
  return [...items].filter(it => elementsOf(it).every(e => app.selection.has(e)));
}

export function selectItems(items) {
  setSelection(items.flatMap(elementsOf));
}

export function setContext(gid) {
  app.context = gid && groupById(gid) ? gid : null;
  emit('context');
}

// Per document tab: which view is showing ('2d' drawing or '3d' preview); remembered per browser
app.tabViews = pref('tabViews', {});
export function setTabView(tabId, view) {
  app.tabViews[tabId] = view;
  savePref('tabViews', app.tabViews);
}
