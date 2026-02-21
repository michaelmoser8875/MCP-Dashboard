"""
MCP Inspector
─────────────
A Flask web app that connects to any MCP server via stdio
and visualizes its tools, resources, and prompts.

Install:
    pip install flask

Usage:
    python app.py -- npx -y @modelcontextprotocol/server-filesystem /tmp
    python app.py -- python my_mcp_server.py
    python app.py -- uvx mcp-server-git --repository /path/to/repo
    python app.py --port 8080 -- node my_server.js

Then open http://localhost:8080
"""

import sys
import json
import argparse
import subprocess
import threading
import webbrowser
from flask import Flask, render_template, jsonify, request


# ── MCP Client ──

class MCPInspector:
    """Connects to an MCP server via stdio and introspects its capabilities."""

    def __init__(self, command: list[str]):
        self.command = command
        self.process = None
        self._id = 0
        self._lock = threading.Lock()
        self._responses: dict = {}
        self._reader_thread = None
        self.server_info = {}
        self.capabilities = {}

    def start(self):
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

        result = self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mcp-inspector", "version": "1.0.0"},
        })
        if result:
            self.server_info = result.get("serverInfo", {})
            self.capabilities = result.get("capabilities", {})

        self._send_notification("notifications/initialized", {})

    def stop(self):
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=5)

    # ── JSON-RPC transport ──

    def _next_id(self):
        with self._lock:
            self._id += 1
            return self._id

    def _send_request(self, method: str, params: dict = None, timeout: float = 10.0):
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
        buf = b""
        while self.process and self.process.poll() is None:
            try:
                chunk = self.process.stdout.read(1)
                if not chunk:
                    break
                buf += chunk

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
                        break

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

    # ── Public query methods ──

    def list_tools(self):
        result = self._send_request("tools/list", {})
        return result.get("tools", []) if result else []

    def list_resources(self):
        result = self._send_request("resources/list", {})
        return result.get("resources", []) if result else []

    def list_prompts(self):
        result = self._send_request("prompts/list", {})
        return result.get("prompts", []) if result else []

    def call_tool(self, name: str, arguments: dict = None):
        return self._send_request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        }, timeout=30.0)

    def read_resource(self, uri: str):
        return self._send_request("resources/read", {"uri": uri})

    def get_prompt(self, name: str, arguments: dict = None):
        return self._send_request("prompts/get", {
            "name": name,
            "arguments": arguments or {},
        })


# ── Flask App ──

app = Flask(__name__)
inspector: MCPInspector = None


@app.route("/")
def index():
    return render_template("index.html")


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
    except Exception:
        return jsonify([])


@app.route("/api/resources")
def api_resources():
    try:
        return jsonify(inspector.list_resources())
    except Exception:
        return jsonify([])


@app.route("/api/prompts")
def api_prompts():
    try:
        return jsonify(inspector.list_prompts())
    except Exception:
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


# ── CLI Entry Point ──

def main():
    parser = argparse.ArgumentParser(
        description="MCP Inspector — visualize any MCP server",
        usage="python app.py [--port PORT] -- <server_command> [args...]",
    )
    parser.add_argument("--port", type=int, default=8080, help="Port for web UI (default: 8080)")
    parser.add_argument("--no-open", action="store_true", help="Don't auto-open browser")

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
        print()
        print("  ⬡ MCP Inspector")
        print("  ────────────────")
        print("  Usage: python app.py -- <server_command> [args...]")
        print()
        print("  Examples:")
        print("    python app.py -- npx -y @modelcontextprotocol/server-filesystem /tmp")
        print("    python app.py -- python my_mcp_server.py")
        print("    python app.py -- uvx mcp-server-git --repository ./repo")
        print("    python app.py --port 8080 -- node my_server.js")
        print()
        sys.exit(1)

    global inspector
    inspector = MCPInspector(server_cmd)

    print()
    print("  ⬡ MCP Inspector")
    print("  ────────────────")
    print(f"  Starting: {' '.join(server_cmd)}")

    try:
        inspector.start()
        name = inspector.server_info.get("name", "unknown")
        print(f"  Connected: {name}")
        print(f"  Dashboard: http://localhost:{args.port}")
        print()
    except Exception as e:
        print(f"  Error: {e}")
        print()
        sys.exit(1)

    if not args.no_open:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()

    try:
        app.run(host="0.0.0.0", port=args.port, debug=False)
    finally:
        inspector.stop()


if __name__ == "__main__":
    main()
