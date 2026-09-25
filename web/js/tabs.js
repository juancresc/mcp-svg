// Tab bar: one tab per open document (server-side), plus client-side 3D preview tabs.

import { app, on } from './state.js';
import { esc } from './ui.js';
import * as actions from './actions.js';

const bar = document.getElementById('tabbar');

export function renderTabs() {
  const s = app.server;
  if (!s) return;
  const html = [];
  for (const t of s.tabs) {
    const active = t.id === s.active;
    html.push(`<div class="tab ${active && app.view === '2d' ? 'active' : ''}" data-tab="${t.id}" data-view="2d"
        title="${esc(t.file || t.local_name || 'Not saved yet')}">
      <span class="tab-name">${esc(t.name)}</span>${t.dirty ? '<span class="tab-dirty" title="Unsaved changes">●</span>' : ''}
      <button class="tab-close" data-close="${t.id}" title="Close">×</button></div>`);
    if (app.previewTabs.has(t.id)) {
      html.push(`<div class="tab ${active && app.view === '3d' ? 'active' : ''}" data-tab="${t.id}" data-view="3d" title="3D preview of ${esc(t.name)}">
        <span class="tab-kind">3D</span><span class="tab-name">${esc(t.name)}</span>
        <button class="tab-close" data-close3d="${t.id}" title="Close preview">×</button></div>`);
    }
  }
  html.push('<button class="tab-new" data-new title="New document">+</button>');
  bar.innerHTML = html.join('');
}

bar.addEventListener('click', (e) => {
  if (e.target.closest('[data-new]')) return actions.newDocument();
  const close = e.target.closest('[data-close]');
  if (close) { e.stopPropagation(); return actions.closeTab(close.dataset.close); }
  const close3d = e.target.closest('[data-close3d]');
  if (close3d) { e.stopPropagation(); return actions.close3d(close3d.dataset.close3d); }
  const tab = e.target.closest('[data-tab]');
  if (tab) actions.switchTab(tab.dataset.tab, tab.dataset.view);
});
// Middle-click closes, like browsers
bar.addEventListener('auxclick', (e) => {
  const tab = e.target.closest('[data-tab]');
  if (e.button !== 1 || !tab) return;
  if (tab.dataset.view === '3d') actions.close3d(tab.dataset.tab);
  else actions.closeTab(tab.dataset.tab);
});

on('doc', renderTabs);
on('view-mode', renderTabs);
