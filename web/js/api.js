// Server client. Every change goes through the server (single source of truth);
// a long-poll keeps this view in sync with edits made by Claude via MCP.

import { app, emit } from './state.js';
import { toast } from './ui.js';

let instance = null;

async function request(method, url, body) {
  const resp = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `${resp.status} ${resp.statusText}`);
  return data;
}

// Accept a server state if it is newer than what we have (responses can arrive out of order,
// and a POST response and the long-poll often carry the same version: render it once).
function accept(state) {
  if (!state) return;
  const sameInstance = state.instance === instance && app.server;
  if (!sameInstance || (state.doc && state.version > app.server.version)) {
    instance = state.instance;
    attachBackground(state.doc);
    app.server = state;
    emit('doc');
  } else if (!state.doc && state.version === app.server.version) {
    Object.assign(app.server, { ...state, doc: app.server.doc });   // metadata only
  }
  // After the doc is applied, so a screenshot shows the latest state
  if (state.screenshot_requested) emit('screenshot-requested');
}

// The reference image is fetched separately and cached by id (it can be megabytes)
const bgCache = { id: null, href: null };
function attachBackground(doc) {
  const bg = doc?.background;
  if (!bg) return;
  if (bg.id === bgCache.id) { bg.href = bgCache.href; return; }
  request('GET', '/api/background').then(r => {
    bgCache.id = bg.id; bgCache.href = r.href;
    if (app.doc?.background?.id === bg.id) { app.doc.background.href = r.href; emit('doc'); }
  }).catch(() => {});
}

function setConnected(ok) {
  if (app.connected !== ok) {
    app.connected = ok;
    emit('connection');
  }
}

// Run a server action; shows errors as toasts and returns the result (or throws on error if strict).
async function call(method, url, body, { strict = false } = {}) {
  try {
    const state = await request(method, url, body);
    setConnected(true);
    accept(state);
    return state;
  } catch (e) {
    toast(e.message, 'error');
    if (strict) throw e;
    return null;
  }
}

// Mutations are sent one at a time, in order (e.g. fast typing in a field: "12" must not land after "120")
let queue = Promise.resolve();
function serial(fn) {
  const run = queue.then(fn, fn);
  queue = run.catch(() => {});
  return run;
}

export const api = {
  ops: (ops, label) => serial(() => call('POST', '/api/ops', { ops, label })),
  undo: () => serial(() => call('POST', '/api/undo', {})),
  redo: () => serial(() => call('POST', '/api/redo', {})),
  newDoc: (width, height, discard) => serial(() => call('POST', '/api/file/new', { width, height, discard })),
  open: (file, discard) => serial(() => call('POST', '/api/file/open', { file, discard })),
  save: (file) => serial(() => call('POST', '/api/file/save', { file })),
  deleteFile: (file) => serial(() => call('POST', '/api/file/delete', { file })),
  importSvg: (svg, opts = {}) => serial(() => call('POST', '/api/file/import', { svg, ...opts })),
  files: () => request('GET', '/api/files'),
  screenshot: (image) => request('POST', '/api/screenshot', { image }),
};

export async function startSync() {
  for (;;) {
    try {
      const q = app.server && instance ? `?since=${app.server.version}&instance=${instance}` : '';
      const state = await request('GET', '/api/state' + q);
      setConnected(true);
      accept(state);
    } catch (e) {
      setConnected(false);
      await new Promise(r => setTimeout(r, 1500));
    }
  }
}
