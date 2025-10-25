#!/usr/bin/env python3
import json, os
from http.server import SimpleHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
from datetime import datetime, timezone

def now_iso():
    return datetime.now(timezone.utc).isoformat()

# --- Runtime State im RAM ---
STATE = {
    "temperatureC": 20.0,
    "light": {"raw": 0, "norm": 0.0},
    "timestamp": now_iso()
}

LED = {
    "on": False,
    "rgb": [255, 160, 0],
    "brightness": 20,
    "updatedAt": None
}

def _clamp_float(v, lo, hi):
    try:
        v = float(v)
    except Exception:
        return None
    return max(lo, min(hi, v))

def _clamp_int_ge0(v):
    try:
        v = int(v)
    except Exception:
        return None
    return max(0, v)

def _clamp_int(v, lo, hi):
    try:
        v = int(v)
    except Exception:
        return None
    return max(lo, min(hi, v))

class Handler(SimpleHTTPRequestHandler):
        # zentrale Helfer
    def _set_api_headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _set_cors_headers_only(self, status=204):
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def end_headers(self):
        if self.path.endswith((".js", ".mjs", ".css", ".glb", ".gltf", ".hdr")):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
            self.send_header("Timing-Allow-Origin", "*")
        super().end_headers()

    def do_OPTIONS(self):
        path = urlparse(self.path).path
        if path in ("/telemetry", "/led"):
            self._set_cors_headers_only(204)
        else:
            self._set_cors_headers_only(204)

    #
    # GET
    #
    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/telemetry":
            self._set_api_headers(200)
            self.wfile.write(json.dumps(STATE).encode("utf-8"))
            return

        if path == "/led":
            self._set_api_headers(200)
            self.wfile.write(json.dumps(LED).encode("utf-8"))
            return

        # alles andere: statische Dateien (index.html, app.js, styles.css, …)
        return super().do_GET()

    #
    # PUT
    #
    def do_PUT(self):
        path = urlparse(self.path).path
        if path not in ("/telemetry", "/led"):
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

        # /telemetry -> Messwerte vom Pi
        if path == "/telemetry":
            # Temperatur
            temp = _clamp_float(payload.get("temperatureC", STATE["temperatureC"]), -20, 60)
            # Licht
            light = payload.get("light", None)
            lr = ln = None
            if isinstance(light, dict):
                lr = _clamp_int_ge0(light.get("raw"))
                ln = _clamp_float(light.get("norm"), 0.0, 1.0)

            ts = payload.get("timestamp", now_iso())

            # State wirklich updaten
            if temp is not None:
                STATE["temperatureC"] = temp
            if lr is not None or ln is not None:
                STATE["light"]["raw"]  = STATE["light"]["raw"]  if lr is None else lr
                STATE["light"]["norm"] = STATE["light"]["norm"] if ln is None else ln
            STATE["timestamp"] = ts
            self._set_api_headers(200)
            self.wfile.write(json.dumps(STATE).encode("utf-8"))
            return
        # /led -> Sollzustand der LED (kommt von Webseite)
        if path == "/led":
            on = payload.get("on", LED["on"])
            rgb = payload.get("rgb", LED["rgb"])
            bri = payload.get("brightness", LED["brightness"])

            if not isinstance(on, bool):
                self._set_api_headers(400)
                self.wfile.write(json.dumps({"error": "on must be boolean"}).encode("utf-8"))
                return

            if (not isinstance(rgb, (list, tuple))) or len(rgb) != 3:
                self._set_api_headers(400)
                self.wfile.write(json.dumps({"error": "rgb must be [r,g,b]"}).encode("utf-8"))
                return

            r = _clamp_int(rgb[0], 0, 255)
            g = _clamp_int(rgb[1], 0, 255)
            b = _clamp_int(rgb[2], 0, 255)
            bri = _clamp_int(bri, 0, 100)

            if None in (r, g, b, bri):
                self._set_api_headers(400)
                self.wfile.write(json.dumps({"error": "invalid rgb/brightness"}).encode("utf-8"))
                return

            LED.update({
                "on": on,
                "rgb": [r, g, b],
                "brightness": bri,
                "updatedAt": now_iso()
            })

            self._set_api_headers(200)
            self.wfile.write(json.dumps(LED).encode("utf-8"))
            return
        self._set_api_headers(400)

def run():
    # Render.com gibt den Port oft als env PORT
    port = int(os.environ.get("PORT", "8123"))
    host = "0.0.0.0"  # wichtig: öffentlich erreichbar, nicht nur localhost
    print(f"Serving on http://{host}:{port}")
    with HTTPServer((host, port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.")

if __name__ == "__main__":
    run()
