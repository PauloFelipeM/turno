"""The panel server: JSON API, event stream (SSE) and the interface, on 127.0.0.1 only.

Reading never writes. The write routes (POST, PUT, DELETE, in `control.py`) need the header `X-Turno: 1` and, with a body,
`Content-Type: application/json`: a page on another site cannot send them without a CORS preflight, which
the server never accepts. Foreign Host and Origin headers are refused so that no page open in the browser
can reach the server through DNS rebinding.
"""

import errno
import json
import queue
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import config
import control
import feed
import panel

WEB = Path(__file__).resolve().parent / "web"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}


class Hub:
    """The event stream subscribers and the watcher that announces changes."""

    def __init__(self, home, homes=None, interval=1.0):
        self.home, self.homes, self.interval = Path(home), homes, interval
        self.cache = {}
        self.subs, self.lock = set(), threading.Lock()
        self.tails = {}  # run id -> (Tail, run)
        self.last = None
        self.stopped = threading.Event()

    def subscribe(self):
        q = queue.Queue(maxsize=500)
        with self.lock:
            self.subs.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.subs.discard(q)

    def publish(self, event, data):
        with self.lock:
            subs = list(self.subs)
        for q in subs:
            try:
                q.put_nowait((event, data))
            except queue.Full:
                pass  # slow browser: it loses events and catches up on the next overview

    def signature(self):
        out = []
        for path in panel.watched_files(self.home):
            try:
                out.append((str(path), path.stat().st_mtime_ns))
            except OSError:
                continue
        return tuple(sorted(out))

    def tick(self):
        sig = self.signature()
        if sig != self.last:
            self.last = sig
            self.publish("overview", panel.overview(self.home, self.cache, self.homes))
        runs = {r["run"]: r for r in panel.live_runs(self.home, self.homes)}
        for run_id, r in runs.items():
            if run_id not in self.tails:
                tail = feed.Tail(r["path"])
                tail.read()  # what already exists comes via /api/live; from here on, via the stream
                self.tails[run_id] = (tail, r)
        for run_id in list(self.tails):
            tail, r = self.tails[run_id]
            items = feed.items(r["engine"], tail.read(), r["root"])
            if items:
                self.publish("feed", {"run": run_id, "unit_id": r["unit_id"], "project": r["project"],
                                      "engine": r["engine"], "items": items})
            if run_id not in runs:
                del self.tails[run_id]

    def run(self):
        while not self.stopped.is_set():
            try:
                self.tick()
            except Exception:
                traceback.print_exc()
            self.stopped.wait(self.interval)

    def stop(self):
        self.stopped.set()


class Handler(BaseHTTPRequestHandler):
    server_version = "turno"

    def log_message(self, fmt, *args):
        pass  # no line per request

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self):
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        return self.headers.get("Host", "") in hosts and (origin is None or origin in {f"http://{h}" for h in hosts})

    def _write(self):
        if not self._allowed() or self.headers.get("X-Turno") != "1":
            return self._send(403, {"error": "forbidden", "message": "missing or invalid headers"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": "bad_request", "message": "invalid Content-Length"})
        if length < 0 or length > control.MAX_BODY:
            return self._send(400, {"error": "bad_request", "message": "body too large"})
        raw = self.rfile.read(length) if length else b""
        if raw and not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._send(403, {"error": "forbidden", "message": "Content-Type must be application/json"})
        try:
            try:
                body = json.loads(raw) if raw.strip() else {}
            except ValueError:
                raise control.bad("invalid JSON")
            if not isinstance(body, dict):
                raise control.bad("the body must be a JSON object")
            parts = [unquote(p) for p in urlsplit(self.path).path.split("/") if p]
            status, out = control.write_route(self.server.hub.home, self.command, parts, body)
            return self._send(status, out)
        except control.ApiError as e:
            return self._send(e.status, {"error": e.code, "message": e.message})
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception:
            traceback.print_exc()
            return self._send(500, {"error": "internal", "message": "internal error"})

    do_POST = do_PUT = do_DELETE = _write

    def do_GET(self):
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        hub = self.server.hub
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        parts = [unquote(p) for p in url.path.split("/") if p]
        try:
            if not parts:
                return self._static("index.html")
            if parts[0] == "static":
                return self._static("/".join(parts[1:]))
            if parts == ["api", "overview"]:
                return self._send(200, panel.overview(hub.home, hub.cache, hub.homes))
            if parts == ["api", "live"]:
                return self._send(200, panel.live(hub.home, hub.homes))
            if parts == ["api", "units"]:
                limit = min(int(query.get("limit", ["50"])[0]), 200)
                return self._send(200, panel.units(hub.home, query.get("project", [None])[0],
                                                   query.get("before", [None])[0], limit))
            if len(parts) == 3 and parts[:2] == ["api", "units"]:
                unit = panel.unit_detail(hub.home, parts[2])
                return self._send(200, unit) if unit else self._send(404, {"error": "not_found"})
            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "setup":
                detail = panel.setup_detail(hub.home, parts[2])
                return self._send(200, detail) if detail is not None else self._send(404, {"error": "not_found"})
            if len(parts) == 3 and parts[:2] == ["api", "projects"]:
                detail = panel.project_detail(hub.home, parts[2], hub.cache)
                return self._send(200, detail) if detail else self._send(404, {"error": "not_found"})
            if parts == ["api", "settings"]:
                return self._send(200, panel.settings(hub.home))
            if parts == ["api", "config"]:
                return self._send(200, panel.config_view(hub.home))
            if parts == ["api", "events"]:
                return self._events(hub)
            return self._send(404, {"error": "not_found"})
        except (BrokenPipeError, ConnectionResetError):
            return None
        except config.ConfigError as e:
            return self._send(500, {"error": "config", "message": str(e)})
        except ValueError:
            return self._send(400, {"error": "bad_request"})
        except Exception:
            traceback.print_exc()
            return self._send(500, {"error": "internal"})

    def _static(self, rel):
        root = WEB.resolve()
        path = (root / rel).resolve()
        if root not in path.parents or not path.is_file():
            return self._send(404, {"error": "not_found"})
        return self._send(200, path.read_bytes(), TYPES.get(path.suffix, "application/octet-stream"))

    def _emit(self, event, data):
        self.wfile.write(f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
        self.wfile.flush()

    def _events(self, hub):
        q = hub.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self._emit("overview", panel.overview(hub.home, hub.cache, hub.homes))
            while not hub.stopped.is_set():
                try:
                    event, data = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")  # keeps the connection alive and finds out whether the browser left
                    self.wfile.flush()
                    continue
                self._emit(event, data)
        except OSError:
            pass
        finally:
            hub.unsubscribe(q)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, hub, port):
        self.hub = hub
        super().__init__(("127.0.0.1", port), Handler)


def serve(home, port):
    hub = Hub(home)
    try:
        srv = Server(hub, port)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            sys.exit(f"port {port} is in use; pick another with --port")
        raise
    threading.Thread(target=hub.run, daemon=True).start()
    print(f"panel at http://localhost:{srv.server_address[1]}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        hub.stop()
        srv.server_close()
