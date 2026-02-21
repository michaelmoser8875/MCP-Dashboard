"""
MCP Inspector
─────────────
A beautiful Flask web app that connects to any MCP server
and visualizes its tools, resources, and prompts.

Install:
    pip install flask mcp

Usage:
    # Inspect a stdio-based MCP server:
    python mcp_inspector.py -- npx -y @modelcontextprotocol/server-filesystem /tmp

    # Inspect a Python MCP server:
    python mcp_inspector.py -- python my_mcp_server.py

    # Inspect with uv:
    python mcp_inspector.py -- uvx mcp-server-git --repository /path/to/repo

    # Then open http://localhost:5000
"""

import sys
import json
import asyncio
import argparse
import subprocess
import threading
from flask import Flask, render_template_string, jsonify, request

# ── MCP Client Logic ──

class MCPInspector:
    """Connects to an MCP server via stdio and introspects its capabilities."""

    def __init__(self, command: list[str]):
        self.command = command
        self.process = None
        self._id = 0
        self._lock = threading.Lock()
        self._responses: dict[int, asyncio.Future] = {}
        self._reader_thread = None
        self.server_info = {}
        self.capabilities = {}

    def start(self):
        """Launch the MCP server subprocess."""
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        # Initialize
        result = self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mcp-inspector", "version": "1.0.0"}
        })
        if result:
            self.server_info = result.get("serverInfo", {})
            self.capabilities = result.get("capabilities", {})
        # Send initialized notification
        self._send_notification("notifications/initialized", {})

    def stop(self):
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=5)

    def _next_id(self):
        with self._lock:
            self._id += 1
            return self._id

    def _send_request(self, method: str, params: dict = None, timeout: float = 10.0):
        """Send a JSON-RPC request and wait for response."""
        msg_id = self._next_id()
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params:
            msg["params"] = params

        event = threading.Event()
        result_holder = [None]

        with self._lock:
            self._responses[msg_id] = (event, result_holder)

        raw = json.dumps(msg)
        content = f"Content-Length: {len(raw)}\r\n\r\n{raw}"
        try:
            self.process.stdin.write(content.encode("utf-8"))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            return None

        if event.wait(timeout=timeout):
            return result_holder[0]
        return None

    def _send_notification(self, method: str, params: dict = None):
        """Send a JSON-RPC notification (no response expected)."""
        msg = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        raw = json.dumps(msg)
        content = f"Content-Length: {len(raw)}\r\n\r\n{raw}"
        try:
            self.process.stdin.write(content.encode("utf-8"))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def _read_loop(self):
        """Read JSON-RPC responses from stdout."""
        buf = b""
        while self.process and self.process.poll() is None:
            try:
                chunk = self.process.stdout.read(1)
                if not chunk:
                    break
                buf += chunk

                # Parse Content-Length header
                while b"\r\n\r\n" in buf:
                    header_end = buf.index(b"\r\n\r\n")
                    header = buf[:header_end].decode("utf-8")
                    content_length = None
                    for line in header.split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            content_length = int(line.split(":")[1].strip())
                    if content_length is None:
                        buf = buf[header_end + 4:]
                        continue

                    body_start = header_end + 4
                    body_end = body_start + content_length

                    if len(buf) < body_end:
                        break  # need more data

                    body = buf[body_start:body_end].decode("utf-8")
                    buf = buf[body_end:]

                    try:
                        data = json.loads(body)
                        msg_id = data.get("id")
                        if msg_id is not None:
                            with self._lock:
                                if msg_id in self._responses:
                                    event, holder = self._responses.pop(msg_id)
                                    if "result" in data:
                                        holder[0] = data["result"]
                                    elif "error" in data:
                                        holder[0] = {"_error": data["error"]}
                                    event.set()
                    except json.JSONDecodeError:
                        pass
            except Exception:
                break

    # ── Public Query Methods ──

    def list_tools(self):
        result = self._send_request("tools/list", {})
        if result and "tools" in result:
            return result["tools"]
        return []

    def list_resources(self):
        result = self._send_request("resources/list", {})
        if result and "resources" in result:
            return result["resources"]
        return []

    def list_resource_templates(self):
        result = self._send_request("resources/templates/list", {})
        if result and "resourceTemplates" in result:
            return result["resourceTemplates"]
        return []

    def list_prompts(self):
        result = self._send_request("prompts/list", {})
        if result and "prompts" in result:
            return result["prompts"]
        return []

    def call_tool(self, name: str, arguments: dict = None):
        result = self._send_request("tools/call", {
            "name": name,
            "arguments": arguments or {}
        }, timeout=30.0)
        return result

    def read_resource(self, uri: str):
        result = self._send_request("resources/read", {"uri": uri})
        return result

    def get_prompt(self, name: str, arguments: dict = None):
        result = self._send_request("prompts/get", {
            "name": name,
            "arguments": arguments or {}
        })
        return result


# ── Flask App ──

app = Flask(__name__)
inspector: MCPInspector = None

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MCP Inspector</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600;700&family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg-0: #06070b;
  --bg-1: #0c0e18;
  --bg-2: #11141f;
  --bg-3: #181c2c;
  --bg-card: #13162080;
  --border: #1e2240;
  --border-hi: #2c3360;
  --tx-1: #e0e3f0;
  --tx-2: #8990ab;
  --tx-3: #505675;
  --cyan: #0ef0dd;
  --cyan-d: rgba(14,240,221,0.08);
  --green: #2dd49e;
  --green-d: rgba(45,212,158,0.08);
  --amber: #f5c030;
  --amber-d: rgba(245,192,48,0.08);
  --violet: #9d8aff;
  --violet-d: rgba(157,138,255,0.08);
  --rose: #ff6082;
  --rose-d: rgba(255,96,130,0.08);
  --mono: 'IBM Plex Mono', monospace;
  --sans: 'Outfit', sans-serif;
  --radius: 12px;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: var(--bg-0); color: var(--tx-1); font-family: var(--sans); min-height: 100vh; }

/* ── Ambient ── */
.glow-1 { position: fixed; top: -15%; left: -10%; width: 600px; height: 600px; background: radial-gradient(circle, rgba(14,240,221,0.04), transparent 70%); pointer-events: none; }
.glow-2 { position: fixed; bottom: -15%; right: -10%; width: 600px; height: 600px; background: radial-gradient(circle, rgba(157,138,255,0.04), transparent 70%); pointer-events: none; }

/* ── Header ── */
header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 18px 32px; border-bottom: 1px solid var(--border);
  background: var(--bg-1); position: sticky; top: 0; z-index: 100;
  backdrop-filter: blur(16px);
}
.logo { display: flex; align-items: center; gap: 14px; }
.logo-mark {
  width: 34px; height: 34px; border-radius: 9px;
  background: linear-gradient(135deg, var(--cyan), var(--violet));
  display: flex; align-items: center; justify-content: center;
  font: 700 13px var(--mono); color: #000;
}
.logo-name { font: 600 15px/1 var(--mono); letter-spacing: 2.5px; }
.logo-name span { color: var(--tx-3); font-weight: 400; font-size: 11px; margin-left: 10px; letter-spacing: 1px; }
.server-badge {
  font: 500 11px var(--mono); color: var(--tx-3); padding: 6px 14px;
  border: 1px solid var(--border); border-radius: 8px; max-width: 400px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

/* ── Layout ── */
.shell { max-width: 1280px; margin: 0 auto; padding: 32px; }

/* ── Stats Row ── */
.stats { display: flex; gap: 12px; margin-bottom: 32px; flex-wrap: wrap; }
.stat-card {
  flex: 1; min-width: 150px; padding: 20px 22px;
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius); backdrop-filter: blur(8px);
  transition: border-color 0.25s;
}
.stat-card:hover { border-color: var(--border-hi); }
.stat-label { font: 600 9px var(--mono); letter-spacing: 2px; text-transform: uppercase; color: var(--tx-3); margin-bottom: 8px; }
.stat-value { font: 700 28px var(--sans); }
.stat-value.cyan { color: var(--cyan); }
.stat-value.green { color: var(--green); }
.stat-value.amber { color: var(--amber); }
.stat-value.violet { color: var(--violet); }

/* ── Tabs ── */
.tabs { display: flex; gap: 4px; margin-bottom: 24px; background: var(--bg-1); padding: 4px; border-radius: 10px; border: 1px solid var(--border); width: fit-content; }
.tab {
  font: 500 13px var(--sans); padding: 10px 22px; border-radius: 8px;
  background: transparent; color: var(--tx-3); border: none;
  cursor: pointer; transition: all 0.2s; letter-spacing: 0.3px;
}
.tab:hover { color: var(--tx-2); }
.tab.active { background: var(--bg-3); color: var(--tx-1); }

.tab-badge {
  font: 600 10px var(--mono); padding: 1px 6px; border-radius: 4px;
  margin-left: 8px; vertical-align: middle;
}
.tab-badge.cyan { background: var(--cyan-d); color: var(--cyan); }
.tab-badge.green { background: var(--green-d); color: var(--green); }
.tab-badge.amber { background: var(--amber-d); color: var(--amber); }
.tab-badge.violet { background: var(--violet-d); color: var(--violet); }

/* ── Panels ── */
.panel { display: none; }
.panel.active { display: block; }

/* ── Card Grid ── */
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 14px; }

.card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius); overflow: hidden;
  backdrop-filter: blur(8px); transition: all 0.25s;
}
.card:hover { border-color: var(--border-hi); transform: translateY(-1px); }

.card-head {
  padding: 18px 22px 14px; display: flex; align-items: flex-start;
  justify-content: space-between; gap: 12px;
}
.card-icon {
  width: 36px; height: 36px; border-radius: 9px;
  display: flex; align-items: center; justify-content: center;
  font-size: 16px; flex-shrink: 0;
}
.card-icon.tool { background: var(--cyan-d); color: var(--cyan); }
.card-icon.resource { background: var(--green-d); color: var(--green); }
.card-icon.prompt { background: var(--amber-d); color: var(--amber); }

.card-info { flex: 1; min-width: 0; }
.card-name { font: 600 14px var(--sans); color: var(--tx-1); margin-bottom: 4px; word-break: break-word; }
.card-desc { font: 400 12.5px var(--sans); color: var(--tx-2); line-height: 1.5; }

.card-body { padding: 0 22px 18px; }

/* ── Schema / Params block ── */
.schema-block {
  background: var(--bg-0); border: 1px solid var(--border);
  border-radius: 8px; padding: 14px 16px; margin-top: 10px;
  font: 400 12px var(--mono); line-height: 1.7; color: var(--tx-2);
  overflow-x: auto; max-height: 300px; overflow-y: auto;
}
.schema-block::-webkit-scrollbar { width: 4px; height: 4px; }
.schema-block::-webkit-scrollbar-thumb { background: var(--border-hi); border-radius: 4px; }

.param-row { display: flex; align-items: baseline; gap: 8px; padding: 4px 0; }
.param-name { color: var(--cyan); font-weight: 500; }
.param-type { color: var(--violet); font-size: 11px; }
.param-req { color: var(--rose); font-size: 9px; letter-spacing: 0.5px; font-weight: 600; }
.param-desc { color: var(--tx-3); font-size: 11.5px; font-family: var(--sans); }

/* ── URI tag ── */
.uri-tag {
  font: 400 11px var(--mono); color: var(--tx-3);
  background: var(--bg-0); padding: 3px 8px; border-radius: 5px;
  border: 1px solid var(--border); display: inline-block;
  margin-top: 6px; word-break: break-all;
}

/* ── Try-it button ── */
.try-btn {
  font: 500 11px var(--mono); padding: 7px 14px; border-radius: 7px;
  border: 1px solid var(--border); background: var(--bg-2);
  color: var(--tx-2); cursor: pointer; transition: all 0.2s;
  margin-top: 12px; letter-spacing: 0.5px;
}
.try-btn:hover { border-color: var(--cyan); color: var(--cyan); background: var(--cyan-d); }

/* ── Result block ── */
.result-block {
  background: var(--bg-0); border: 1px solid var(--border);
  border-radius: 8px; padding: 14px 16px; margin-top: 10px;
  font: 400 12px var(--mono); line-height: 1.7; color: var(--green);
  max-height: 250px; overflow-y: auto; white-space: pre-wrap;
  word-break: break-word; display: none;
}

/* ── Empty state ── */
.empty {
  text-align: center; padding: 60px 20px; color: var(--tx-3);
  font: 400 14px var(--sans);
}
.empty-icon { font-size: 32px; margin-bottom: 12px; opacity: 0.5; }

/* ── Loading ── */
.loading { text-align: center; padding: 80px; }
.spinner {
  width: 32px; height: 32px; border: 2.5px solid var(--border);
  border-top-color: var(--cyan); border-radius: 50%;
  animation: spin 0.8s linear infinite; margin: 0 auto 16px;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ── Animations ── */
@keyframes fadeUp {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: translateY(0); }
}
.card { animation: fadeUp 0.4s ease both; }
.card:nth-child(1) { animation-delay: 0.02s; }
.card:nth-child(2) { animation-delay: 0.06s; }
.card:nth-child(3) { animation-delay: 0.10s; }
.card:nth-child(4) { animation-delay: 0.14s; }
.card:nth-child(5) { animation-delay: 0.18s; }
.card:nth-child(6) { animation-delay: 0.22s; }

/* ── Responsive ── */
@media (max-width: 500px) {
  .shell { padding: 16px; }
  .card-grid { grid-template-columns: 1fr; }
  header { padding: 14px 16px; }
}
</style>
</head>
<body>

<div class="glow-1"></div>
<div class="glow-2"></div>

<header>
  <div class="logo">
    <div class="logo-mark">⬡</div>
    <div class="logo-name">MCP INSPECTOR <span>v1.0</span></div>
  </div>
  <div class="server-badge" id="server-badge">connecting...</div>
</header>

<div class="shell">
  <!-- Stats -->
  <div class="stats" id="stats">
    <div class="stat-card"><div class="stat-label">Server</div><div class="stat-value" id="stat-server" style="font-size:16px; color:var(--tx-2)">—</div></div>
    <div class="stat-card"><div class="stat-label">Tools</div><div class="stat-value cyan" id="stat-tools">—</div></div>
    <div class="stat-card"><div class="stat-label">Resources</div><div class="stat-value green" id="stat-resources">—</div></div>
    <div class="stat-card"><div class="stat-label">Prompts</div><div class="stat-value amber" id="stat-prompts">—</div></div>
  </div>

  <!-- Tabs -->
  <div class="tabs">
    <button class="tab active" data-panel="tools">⚡ Tools <span class="tab-badge cyan" id="tab-tools-count">0</span></button>
    <button class="tab" data-panel="resources">◆ Resources <span class="tab-badge green" id="tab-resources-count">0</span></button>
    <button class="tab" data-panel="prompts">◈ Prompts <span class="tab-badge amber" id="tab-prompts-count">0</span></button>
  </div>

  <!-- Tools Panel -->
  <div class="panel active" id="panel-tools">
    <div class="loading" id="loading-tools"><div class="spinner"></div><div style="color:var(--tx-3)">Loading tools...</div></div>
    <div class="card-grid" id="grid-tools"></div>
  </div>

  <!-- Resources Panel -->
  <div class="panel" id="panel-resources">
    <div class="loading" id="loading-resources"><div class="spinner"></div><div style="color:var(--tx-3)">Loading resources...</div></div>
    <div class="card-grid" id="grid-resources"></div>
  </div>

  <!-- Prompts Panel -->
  <div class="panel" id="panel-prompts">
    <div class="loading" id="loading-prompts"><div class="spinner"></div><div style="color:var(--tx-3)">Loading prompts...</div></div>
    <div class="card-grid" id="grid-prompts"></div>
  </div>
</div>

<script>
// ── Tab switching ──
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

// ── Render tools ──
function renderTools(tools) {
  const grid = document.getElementById('grid-tools');
  document.getElementById('loading-tools').style.display = 'none';
  if (!tools.length) { grid.innerHTML = '<div class="empty"><div class="empty-icon">⚡</div>No tools exposed by this server</div>'; return; }

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

// ── Render resources ──
function renderResources(resources) {
  const grid = document.getElementById('grid-resources');
  document.getElementById('loading-resources').style.display = 'none';
  if (!resources.length) { grid.innerHTML = '<div class="empty"><div class="empty-icon">◆</div>No resources exposed by this server</div>'; return; }

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

// ── Render prompts ──
function renderPrompts(prompts) {
  const grid = document.getElementById('grid-prompts');
  document.getElementById('loading-prompts').style.display = 'none';
  if (!prompts.length) { grid.innerHTML = '<div class="empty"><div class="empty-icon">◈</div>No prompts exposed by this server</div>'; return; }

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
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({name, arguments: {}})
    });
    const data = await res.json();
    block.textContent = JSON.stringify(data, null, 2);
    block.style.color = data.error ? 'var(--rose)' : 'var(--green)';
  } catch(e) { block.textContent = 'Error: ' + e.message; block.style.color = 'var(--rose)'; }
}

async function readResource(uri, btn) {
  const block = btn.nextElementSibling;
  block.style.display = 'block';
  block.textContent = 'Reading resource...';
  block.style.color = 'var(--tx-3)';
  try {
    const res = await fetch('/api/resources/read', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({uri})
    });
    const data = await res.json();
    block.textContent = JSON.stringify(data, null, 2);
    block.style.color = data.error ? 'var(--rose)' : 'var(--green)';
  } catch(e) { block.textContent = 'Error: ' + e.message; block.style.color = 'var(--rose)'; }
}

async function getPrompt(name, btn) {
  const block = btn.nextElementSibling;
  block.style.display = 'block';
  block.textContent = 'Getting prompt...';
  block.style.color = 'var(--tx-3)';
  try {
    const res = await fetch('/api/prompts/get', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({name, arguments: {}})
    });
    const data = await res.json();
    block.textContent = JSON.stringify(data, null, 2);
    block.style.color = data.error ? 'var(--rose)' : 'var(--green)';
  } catch(e) { block.textContent = 'Error: ' + e.message; block.style.color = 'var(--rose)'; }
}

// ── Load everything ──
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
  } catch(e) {
    console.error('Init error:', e);
    document.getElementById('server-badge').textContent = 'connection failed';
  }
}

init();
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/info")
def api_info():
    return jsonify({
        "name": inspector.server_info.get("name", "Unknown"),
        "version": inspector.server_info.get("version", ""),
        "capabilities": inspector.capabilities,
        "command": " ".join(inspector.command),
    })


@app.route("/api/tools")
def api_tools():
    try:
        return jsonify(inspector.list_tools())
    except Exception as e:
        return jsonify([])


@app.route("/api/resources")
def api_resources():
    try:
        return jsonify(inspector.list_resources())
    except Exception as e:
        return jsonify([])


@app.route("/api/prompts")
def api_prompts():
    try:
        return jsonify(inspector.list_prompts())
    except Exception as e:
        return jsonify([])


@app.route("/api/tools/call", methods=["POST"])
def api_call_tool():
    data = request.json
    try:
        result = inspector.call_tool(data["name"], data.get("arguments", {}))
        return jsonify(result or {"error": "No response"})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/resources/read", methods=["POST"])
def api_read_resource():
    data = request.json
    try:
        result = inspector.read_resource(data["uri"])
        return jsonify(result or {"error": "No response"})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/prompts/get", methods=["POST"])
def api_get_prompt():
    data = request.json
    try:
        result = inspector.get_prompt(data["name"], data.get("arguments", {}))
        return jsonify(result or {"error": "No response"})
    except Exception as e:
        return jsonify({"error": str(e)})


def main():
    parser = argparse.ArgumentParser(
        description="MCP Inspector — visualize any MCP server",
        usage="python mcp_inspector.py [--port PORT] -- <server_command> [args...]"
    )
    parser.add_argument("--port", type=int, default=5000, help="Port for web UI (default: 5000)")
    parser.add_argument("--no-open", action="store_true", help="Don't auto-open browser")

    # Split on -- to separate our args from the server command
    argv = sys.argv[1:]
    if "--" in argv:
        split_idx = argv.index("--")
        our_args = argv[:split_idx]
        server_cmd = argv[split_idx + 1:]
    else:
        our_args = argv
        server_cmd = []

    args = parser.parse_args(our_args)

    if not server_cmd:
        print("\n  MCP Inspector")
        print("  ─────────────")
        print("  Usage: python mcp_inspector.py -- <server_command> [args...]\n")
        print("  Examples:")
        print("    python mcp_inspector.py -- npx -y @modelcontextprotocol/server-filesystem /tmp")
        print("    python mcp_inspector.py -- python my_mcp_server.py")
        print("    python mcp_inspector.py -- uvx mcp-server-git --repository ./repo")
        print("    python mcp_inspector.py --port 8080 -- node my_server.js\n")
        sys.exit(1)

    global inspector
    inspector = MCPInspector(server_cmd)

    print(f"\n  ⬡ MCP Inspector")
    print(f"  ───────────────")
    print(f"  Starting: {' '.join(server_cmd)}")

    try:
        inspector.start()
        name = inspector.server_info.get("name", "unknown")
        print(f"  Connected: {name}")
        print(f"  Dashboard: http://localhost:{args.port}\n")
    except Exception as e:
        print(f"  Error connecting to server: {e}\n")
        sys.exit(1)

    if not args.no_open:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()

    try:
        app.run(host="0.0.0.0", port=args.port, debug=False)
    finally:
        inspector.stop()


if __name__ == "__main__":
    main()