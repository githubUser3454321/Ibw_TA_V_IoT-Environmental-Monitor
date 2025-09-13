#!/usr/bin/env python3
import json
from http.server import SimpleHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
from datetime import datetime, timezone

STATE = {
    "temperatureC": 20.0,
    "axes": {"x": 0.0, "y": 75.0, "z": 2.0},
    "timestamp": datetime.now(timezone.utc).isoformat()
}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

class Handler(SimpleHTTPRequestHandler):
    # zentrale Helfer
    def _set_api_headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # >>> CORS auch für statische Module erlauben <<<
    def end_headers(self):
        # Für Module/Assets CORS erlauben (wenn von anderem Origin geladen)
        if self.path.endswith((".js", ".mjs", ".css", ".glb", ".gltf", ".hdr")):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
            self.send_header("Timing-Allow-Origin", "*")
        super().end_headers()

    def do_OPTIONS(self):
        path = urlparse(self.path).path
        if path == "/telemetry":
            self._set_api_headers(204)
        else:
            # Statische Preflight-Anfragen (falls Browser welche schickt)
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/telemetry":
            self._set_api_headers(200)
            self.wfile.write(json.dumps(STATE).encode("utf-8"))
        else:
            # Statisch ausliefern (CORS-Header kommen in end_headers())
            return super().do_GET()

    def do_PUT(self):
        path = urlparse(self.path).path
        if path != "/telemetry":
            self._set_api_headers(404)
            self.wfile.write(json.dumps({"error": "not found"}).encode("utf-8"))
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._set_api_headers(400)
            self.wfile.write(json.dumps({"error": "invalid json", "detail": str(e)}).encode("utf-8"))
            return

        # Validierung/Clamping
        def clamp(v, lo, hi):
            try: v = float(v)
            except: return None
            return max(lo, min(hi, v))

        temp = clamp(payload.get("temperatureC", STATE["temperatureC"]), -20, 60)
        axes = payload.get("axes", STATE["axes"])
        nx = clamp(axes.get("x", STATE["axes"]["x"]), -180, 180)
        ny = clamp(axes.get("y", STATE["axes"]["y"]), 0, 180)
        nz = clamp(axes.get("z", STATE["axes"]["z"]), 0.4, 5.0)
        ts = payload.get("timestamp", now_iso())

        STATE["temperatureC"] = STATE["temperatureC"] if temp is None else temp
        STATE["axes"] = {
            "x": STATE["axes"]["x"] if nx is None else nx,
            "y": STATE["axes"]["y"] if ny is None else ny,
            "z": STATE["axes"]["z"] if nz is None else nz,
        }
        STATE["timestamp"] = ts

        self._set_api_headers(200)
        self.wfile.write(json.dumps(STATE).encode("utf-8"))

def run(host="localhost", port=8123):
    print(f"Serving on http://{host}:{port}  (GET/PUT /telemetry) + static files")
    with HTTPServer((host, port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.")

if __name__ == "__main__":
    run()
