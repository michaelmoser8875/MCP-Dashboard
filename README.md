# ⬡ MCP Dashboard

A lightweight Flask dashboard for inspecting MCP servers. Instead of reading config files or guessing what an MCP server exposes, you can point this at any MCP server and instantly browse its tools, resources, and prompts in one place.

![Python](https://img.shields.io/badge/python-3.10+-blue) ![Flask](https://img.shields.io/badge/flask-required-green) ![License](https://img.shields.io/badge/license-MIT-gray)

## What it does

Point it at any MCP server and it will:

- List all **tools** with their parameters, types, and descriptions
- List all **resources** with URIs and MIME types
- List all **prompts** with their arguments
- Let you **try tools**, **read resources**, and **get prompts** directly from the browser

## Setup

```bash
pip install flask
```

## Usage

```bash
python app.py -- <your_mcp_server_command>
```

The `--` separates the inspector's options from the MCP server command you want to inspect.

### Examples

```bash
# Filesystem server
python app.py -- npx -y @modelcontextprotocol/server-filesystem /tmp

# Your own Python MCP server
python app.py -- python my_server.py

# A uv-managed server
python app.py -- uvx mcp-server-git --repository ./my-repo

# Custom port
python app.py --port 3000 -- npx -y @modelcontextprotocol/server-filesystem /tmp
```

The dashboard opens automatically at **http://localhost:8080**.

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `8080` | Port for the web dashboard |
| `--no-open` | off | Don't auto-open the browser |

## Project structure

```
mcp_inspector/
├── app.py              # Flask server + MCP client
├── templates/
│   └── index.html      # Page template
└── static/
    ├── style.css       # Styling
    └── app.js          # Frontend logic
```

## How it works

The inspector launches your MCP server as a subprocess and talks to it over **stdio** using the MCP JSON-RPC protocol. It sends `initialize`, then queries `tools/list`, `resources/list`, and `prompts/list` to discover what the server offers. The Flask app serves a dashboard that displays everything and lets you interact with it.

## License

MIT
