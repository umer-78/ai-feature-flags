"""HTTP API over a Platform, standard library only.

GET  /flags/<name>?user=<id>[&<attribute>=<value>...]    the variant for this request
POST /flags/<name>/score    {"user", "variant", "score", "segment"?, "attributes"?}
GET  /flags/<name>/status   the rollout: stage, arms, decision, log
POST /flags/<name>/enabled  {"enabled": false} is the kill switch
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def make_server(platform, host="127.0.0.1", port=8000):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def send(self, code, body):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def route(self, method):
            url = urlparse(self.path)
            parts = url.path.strip("/").split("/")
            if len(parts) not in (2, 3) or parts[0] != "flags" or parts[1] not in platform.flags:
                return self.send(404, {"error": "no such flag"})
            name, action = parts[1], parts[2] if len(parts) == 3 else ""
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}") if method == "POST" else {}
                with lock:
                    if (method, action) == ("GET", ""):
                        query = {k: v[0] for k, v in parse_qs(url.query).items()}
                        return self.send(200, platform.evaluate(name, query.pop("user"), query))
                    if (method, action) == ("GET", "status"):
                        return self.send(200, platform.status(name))
                    if (method, action) == ("POST", "score"):
                        return self.send(200, platform.score(name, body["user"], body["variant"], body["score"],
                                                             body.get("segment"), body.get("attributes")))
                    if (method, action) == ("POST", "enabled"):
                        return self.send(200, platform.set_enabled(name, body["enabled"]))
            except (KeyError, ValueError, TypeError) as e:
                return self.send(400, {"error": f"{type(e).__name__}: {e}"})
            self.send(404, {"error": "not found"})

        def do_GET(self):
            self.route("GET")

        def do_POST(self):
            self.route("POST")

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer((host, port), Handler)
