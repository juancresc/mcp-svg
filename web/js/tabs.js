// Tab bar: one tab per open document (server-side). Each tab has two views of the same
// file — Drawing (2D) and 3D — switched with the segmented control; the choice is remembered per tab.

import { app, on } from './state.js';
import { esc } from './ui.js';
import * as actions from './actions.js';

const bar = document.getElementById('tabbar');

export function renderTabs() {
  const s = app.server;
  if (!s) return;
  const html = s.tabs.map(t => {
    const active = t.id === s.active;
    const view = app.tabViews[t.id] || '2d';
    return `<div class="tab ${active ? 'active' : ''}" data-tab="${esc(t.id)}" title="${esc(t.file || t.local_name || 'Not saved yet')}">
      <span class="tab-name">${esc(t.name)}</span>${t.dirty ? '<span class="tab-dirty" title="Unsaved changes">●</span>' : ''}
      ${active ? `<span class="tab-views" role="tablist">
          <button class="${view === '2d' ? 'on' : ''}" data-view="2d" title="Drawing (2D)">2D</button><button
            class="${view === '3d' ? 'on' : ''}" data-view="3d" title="3D preview (3)">3D</button></span>`
        : (view === '3d' ? '<span class="tab-kind">3D</span>' : '')}
      <button class="tab-close" data-close="${esc(t.id)}" title="Close">×</button></div>`;
  });
  html.push('<button class="tab-new" data-new title="New document">+</button>');
  bar.innerHTML = html.join('');
}

bar.addEventListener('click', (e) => {
  if (e.target.closest('[data-new]')) return actions.newDocument();
  const close = e.target.closest('[data-close]');
  if (close) { e.stopPropagation(); return actions.closeTab(close.dataset.close); }
  const tab = e.target.closest('[data-tab]');
  if (!tab) return;
  const view = e.target.closest('[data-view]')?.dataset.view;
  actions.switchTab(tab.dataset.tab, view);
});
bar.addEventListener('auxclick', (e) => {   // middle-click closes, like browsers
  const tab = e.target.closest('[data-tab]');
  if (e.button === 1 && tab) actions.closeTab(tab.dataset.tab);
});

on('doc', renderTabs);
on('view-mode', renderTabs);
