// ── Tab Switching ──
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('panel-' + tab.dataset.panel).classList.add('active');
  });
});

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

// ── Render Tools ──
function renderTools(tools) {
  const grid = document.getElementById('grid-tools');
  document.getElementById('loading-tools').style.display = 'none';

  if (!tools.length) {
    grid.innerHTML = '<div class="empty"><div class="empty-icon">⚡</div>No tools exposed by this server</div>';
    return;
  }

  grid.innerHTML = tools.map((t, i) => {
    const schema = t.inputSchema;
    const props = schema?.properties || {};
    const required = schema?.required || [];
    const paramCount = Object.keys(props).length;

    let paramsHtml = '';
    if (paramCount) {
      paramsHtml = '<div class="schema-block">' + Object.entries(props).map(([name, prop]) => {
        const isReq = required.includes(name);
        return `<div class="param-row">
          <span class="param-name">${escHtml(name)}</span>
          <span class="param-type">${escHtml(prop.type || 'any')}</span>
          ${isReq ? '<span class="param-req">REQUIRED</span>' : ''}
          ${prop.description ? `<span class="param-desc">— ${escHtml(prop.description)}</span>` : ''}
        </div>`;
      }).join('') + '</div>';
    }

    return `<div class="card">
      <div class="card-head">
        <div class="card-icon tool">⚡</div>
        <div class="card-info">
          <div class="card-name">${escHtml(t.name)}</div>
          <div class="card-desc">${escHtml(t.description || 'No description')}</div>
        </div>
      </div>
      <div class="card-body">
        ${paramCount ? `<div style="font: 500 10px var(--mono); color: var(--tx-3); letter-spacing: 1px; text-transform: uppercase; margin-bottom: 2px;">${paramCount} parameter${paramCount > 1 ? 's' : ''}</div>` : ''}
        ${paramsHtml}
        <button class="try-btn" onclick="tryTool('${escHtml(t.name)}', this)">▶ Try it</button>
        <div class="result-block" id="result-tool-${i}"></div>
      </div>
    </div>`;
  }).join('');
}

// ── Render Resources ──
function renderResources(resources) {
  const grid = document.getElementById('grid-resources');
  document.getElementById('loading-resources').style.display = 'none';

  if (!resources.length) {
    grid.innerHTML = '<div class="empty"><div class="empty-icon">◆</div>No resources exposed by this server</div>';
    return;
  }

  grid.innerHTML = resources.map((r, i) => {
    return `<div class="card">
      <div class="card-head">
        <div class="card-icon resource">◆</div>
        <div class="card-info">
          <div class="card-name">${escHtml(r.name || r.uri)}</div>
          <div class="card-desc">${escHtml(r.description || 'No description')}</div>
        </div>
      </div>
      <div class="card-body">
        <div class="uri-tag">${escHtml(r.uri)}</div>
        ${r.mimeType ? `<div style="font: 400 11px var(--mono); color: var(--tx-3); margin-top: 6px;">${escHtml(r.mimeType)}</div>` : ''}
        <button class="try-btn" onclick="readResource('${escHtml(r.uri)}', this)">◆ Read</button>
        <div class="result-block" id="result-res-${i}"></div>
      </div>
    </div>`;
  }).join('');
}

// ── Render Prompts ──
function renderPrompts(prompts) {
  const grid = document.getElementById('grid-prompts');
  document.getElementById('loading-prompts').style.display = 'none';

  if (!prompts.length) {
    grid.innerHTML = '<div class="empty"><div class="empty-icon">◈</div>No prompts exposed by this server</div>';
    return;
  }

  grid.innerHTML = prompts.map((p, i) => {
    const args = p.arguments || [];
    let argsHtml = '';
    if (args.length) {
      argsHtml = '<div class="schema-block">' + args.map(a => {
        return `<div class="param-row">
          <span class="param-name">${escHtml(a.name)}</span>
          ${a.required ? '<span class="param-req">REQUIRED</span>' : ''}
          ${a.description ? `<span class="param-desc">— ${escHtml(a.description)}</span>` : ''}
        </div>`;
      }).join('') + '</div>';
    }

    return `<div class="card">
      <div class="card-head">
        <div class="card-icon prompt">◈</div>
        <div class="card-info">
          <div class="card-name">${escHtml(p.name)}</div>
          <div class="card-desc">${escHtml(p.description || 'No description')}</div>
        </div>
      </div>
      <div class="card-body">
        ${args.length ? `<div style="font: 500 10px var(--mono); color: var(--tx-3); letter-spacing: 1px; text-transform: uppercase; margin-bottom: 2px;">${args.length} argument${args.length > 1 ? 's' : ''}</div>` : ''}
        ${argsHtml}
        <button class="try-btn" onclick="getPrompt('${escHtml(p.name)}', this)">◈ Get</button>
        <div class="result-block" id="result-prompt-${i}"></div>
      </div>
    </div>`;
  }).join('');
}

// ── API Calls ──
async function tryTool(name, btn) {
  const block = btn.nextElementSibling;
  block.style.display = 'block';
  block.textContent = 'Calling tool...';
  block.style.color = 'var(--tx-3)';
  try {
    const res = await fetch('/api/tools/call', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, arguments: {} })
    });
    const data = await res.json();
    block.textContent = JSON.stringify(data, null, 2);
    block.style.color = data.error ? 'var(--rose)' : 'var(--green)';
  } catch (e) {
    block.textContent = 'Error: ' + e.message;
    block.style.color = 'var(--rose)';
  }
}

async function readResource(uri, btn) {
  const block = btn.nextElementSibling;
  block.style.display = 'block';
  block.textContent = 'Reading resource...';
  block.style.color = 'var(--tx-3)';
  try {
    const res = await fetch('/api/resources/read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ uri })
    });
    const data = await res.json();
    block.textContent = JSON.stringify(data, null, 2);
    block.style.color = data.error ? 'var(--rose)' : 'var(--green)';
  } catch (e) {
    block.textContent = 'Error: ' + e.message;
    block.style.color = 'var(--rose)';
  }
}

async function getPrompt(name, btn) {
  const block = btn.nextElementSibling;
  block.style.display = 'block';
  block.textContent = 'Getting prompt...';
  block.style.color = 'var(--tx-3)';
  try {
    const res = await fetch('/api/prompts/get', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, arguments: {} })
    });
    const data = await res.json();
    block.textContent = JSON.stringify(data, null, 2);
    block.style.color = data.error ? 'var(--rose)' : 'var(--green)';
  } catch (e) {
    block.textContent = 'Error: ' + e.message;
    block.style.color = 'var(--rose)';
  }
}

// ── Load Everything ──
async function init() {
  try {
    const info = await (await fetch('/api/info')).json();
    document.getElementById('stat-server').textContent = info.name || 'Unknown';
    document.getElementById('server-badge').textContent = info.command || '—';

    const [tools, resources, prompts] = await Promise.all([
      fetch('/api/tools').then(r => r.json()),
      fetch('/api/resources').then(r => r.json()),
      fetch('/api/prompts').then(r => r.json()),
    ]);

    document.getElementById('stat-tools').textContent = tools.length;
    document.getElementById('stat-resources').textContent = resources.length;
    document.getElementById('stat-prompts').textContent = prompts.length;
    document.getElementById('tab-tools-count').textContent = tools.length;
    document.getElementById('tab-resources-count').textContent = resources.length;
    document.getElementById('tab-prompts-count').textContent = prompts.length;

    renderTools(tools);
    renderResources(resources);
    renderPrompts(prompts);
  } catch (e) {
    console.error('Init error:', e);
    document.getElementById('server-badge').textContent = 'connection failed';
  }
}

init();
