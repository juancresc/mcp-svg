// "Connect Claude": copy-paste setups for Claude Code / Claude Desktop (MCP) or a plain curl
// prompt, built from the addresses the server reports (works locally and when deployed).

import { modal, toast, esc } from './ui.js';

const q = (s) => `'${s.replace(/'/g, `'\\''`)}'`;   // shell single-quote

function snippets({ editor_url, api_url, mcp_url, token }) {
  const auth = token ? `Bearer ${token}` : null;
  const curlAuth = token ? ` -H ${q('Authorization: ' + auth)}` : '';
  const json = (o) => JSON.stringify(o, null, 2);
  const server = { type: 'sse', url: mcp_url, ...(auth ? { headers: { Authorization: auth } } : {}) };
  return [
    {
      id: 'code', label: 'Claude Code',
      intro: 'Run this in a terminal once. Then start <code>claude</code> and check the connection with <code>/mcp</code>.',
      text: `claude mcp add --transport sse kerf ${mcp_url}${auth ? ` --header ${q('Authorization: ' + auth)}` : ''}`,
    },
    {
      id: 'project', label: '.mcp.json',
      intro: 'Or save this as <code>.mcp.json</code> in a project folder, so everyone who runs Claude Code there gets the editor.',
      text: json({ mcpServers: { kerf: server } }),
    },
    {
      id: 'desktop', label: 'Claude Desktop',
      intro: 'Claude Desktop → Settings → Developer → Edit Config (<code>claude_desktop_config.json</code>). Add this and restart it. It needs Node.js: <code>mcp-remote</code> bridges the SSE server.',
      text: json({ mcpServers: { kerf: { command: 'npx', args: ['-y', 'mcp-remote', mcp_url, ...(auth ? ['--header', `Authorization: ${auth}`] : [])] } } }),
    },
    {
      id: 'curl', label: 'Prompt (curl, no MCP)',
      intro: 'No MCP set up? Paste this into Claude (any version that can run shell commands). It explains the HTTP API.',
      text: `You can drive my Kerf CNC editor over HTTP with curl. I watch every change live at ${editor_url}
Units: 1 = 1 mm. Every POST needs -H 'Content-Type: application/json'${token ? `, and every request needs${curlAuth}` : ''}.

Read the active document (tabs, layers, elements, entities, material):
  curl -s${curlAuth} ${api_url}/state
Edit — a batch of ops is one undo step; "$0" means the id created by op 0 of the same batch:
  curl -s${curlAuth} -H 'Content-Type: application/json' -X POST ${api_url}/ops -d '{"label":"Add plate","ops":[
    {"op":"add_element","tag":"rect","layer":"CUT_OUTSIDE","attrs":{"x":10,"y":10,"width":200,"height":100,"rx":5,"fill":"none"}},
    {"op":"add_element","tag":"circle","layer":"CUT_INSIDE","attrs":{"cx":30,"cy":30,"r":4,"fill":"none"}},
    {"op":"group","items":["$0","$1"],"name":"Plate"}]}'
Ops: add_element{tag,attrs,layer,text?,group?} · update_element{id,attrs?,text?,layer?} · remove_elements{ids}
  · group{items,name} · ungroup{id} · update_group{id,name?,qty?,assembly?} · add_layer{name,color,line_style?,export?,depth?}
  · update_layer{name,...} · set_size{width,height} · set_material{material} · set_title{title}
  · set_params{params} · import_svg{svg,layer?} · clear
Undo / redo:            curl -s${curlAuth} -H 'Content-Type: application/json' -X POST ${api_url}/undo -d '{}'   (or /redo)
New tab:                ... -X POST ${api_url}/file/new -d '{"width":800,"height":600}'
Open / save (data dir): ... -X POST ${api_url}/file/open -d '{"file":"desk/desk.kerf"}'  ·  ${api_url}/file/save -d '{"file":"my-part"}'
List files:             curl -s${curlAuth} ${api_url}/files
Exports:                curl -s${curlAuth} ${api_url}/export/cnc (SVG in mm) · /export/cnc-dxf · /export/parts (zip) · /export/project (.kerf)

Rules: part outlines on CUT_OUTSIDE, holes/slots on CUT_INSIDE, pockets on a layer with a depth, labels on NOTES.
Use fill="none"; never set stroke colours (the layer decides). Group each part's outline + holes into an entity.
Don't discard my unsaved work or close my tabs without asking.`,
    },
  ];
}

export async function connectDialog() {
  let info;
  try {
    const r = await fetch('/api/connect');
    info = await r.json();
    if (!r.ok) throw new Error(info.error || r.statusText);
  } catch (e) {
    return toast(`Couldn't read the server addresses: ${e.message}`, 'error');
  }
  const items = snippets(info);
  const local = /^http:\/\/(localhost|127\.0\.0\.1)[:/]/.test(info.editor_url);
  let current = 0;
  await modal({
    title: 'Connect Claude',
    html: `<div class="connect">
      <div class="seg connect-tabs">${items.map((it, i) => `<button type="button" class="btn ${i === 0 ? 'on' : ''}" data-snip="${i}">${esc(it.label)}</button>`).join('')}</div>
      <p class="hint connect-intro"></p>
      <div class="connect-code"><pre></pre><button type="button" class="btn primary" data-copy>Copy</button></div>
      <p class="hint">Editor: <code>${esc(info.editor_url)}</code> · MCP (SSE): <code>${esc(info.mcp_url)}</code>
        ${info.token ? '<br><b>These snippets contain your access token.</b> Only share them with people who may edit your files.' : ''}
        ${local ? '<br>This editor only answers on this computer. To use it from elsewhere, deploy it with <code>KERF_PUBLIC_URL</code> and <code>KERF_TOKEN</code> (see the README).' : ''}</p>
    </div>`,
    buttons: [{ label: 'Close', value: true, kind: 'primary' }],
    setup: (form) => {
      const show = (i) => {
        current = i;
        form.querySelectorAll('.connect-tabs button').forEach((b, j) => b.classList.toggle('on', j === i));
        form.querySelector('.connect-intro').innerHTML = items[i].intro;
        form.querySelector('.connect-code pre').textContent = items[i].text;
      };
      form.querySelector('.connect-tabs').addEventListener('click', (e) => {
        const b = e.target.closest("[data-snip]");
        if (b) show(+b.dataset.snip);
      });
      form.querySelector('[data-copy]').addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(items[current].text);
          toast('Copied', 'ok');
        } catch (_) {   // clipboard needs https or localhost: select the text instead
          const range = document.createRange();
          range.selectNodeContents(form.querySelector('.connect-code pre'));
          getSelection().removeAllRanges(); getSelection().addRange(range);
          toast('Press ⌘C / Ctrl+C to copy the selected text');
        }
      });
      show(0);
    },
  });
}
