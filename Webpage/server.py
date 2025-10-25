#!/usr/bin/env python3
import json
from http.server import SimpleHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
from datetime import datetime, timezone

def now_iso():
    return datetime.now(timezone.utc).isoformat()

# --- Neues STATE-Schema: Temperatur + Licht ---
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
    try: v = int(v)
    except: return None
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

    # >>> CORS auch für statische Module erlauben <<<
    def end_headers(self):
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
            return
        if path == "/led":
            self._set_api_headers(200)
            self.wfile.write(json.dumps(LED).encode("utf-8"))
            return
        return super().do_GET()

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

        # --- Temperatur clampen ---
        temp = _clamp_float(payload.get("temperatureC", STATE["temperatureC"]), -20, 60)

        # --- Licht lesen (neu) ---
        light = payload.get("light", None)
        lr = ln = None
        if isinstance(light, dict):
            lr = _clamp_int_ge0(light.get("raw"))
            ln = _clamp_float(light.get("norm"), 0.0, 1.0)

        if path == "/telemetry":
            ts = payload.get("timestamp", now_iso())

            # --- STATE aktualisieren (nur wenn Werte valide sind) ---
            if temp is not None:
                STATE["temperatureC"] = temp
            if lr is not None or ln is not None:
                STATE["light"]["raw"]  = STATE["light"]["raw"]  if lr is None else lr
                STATE["light"]["norm"] = STATE["light"]["norm"] if ln is None else ln
            STATE["timestamp"] = ts
            self._set_api_headers(200)
            self.wfile.write(json.dumps(STATE).encode("utf-8"))
            return
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

            LED.update({"on": on, "rgb": [r, g, b], "brightness": bri, "updatedAt": now_iso()})
            self._set_api_headers(200)
            self.wfile.write(json.dumps(LED).encode("utf-8"))
            return
        self._set_api_headers(400)



def run(host="localhost", port=8123):
    print(f"Serving on http://{host}:{port}  (GET/PUT /telemetry) + static files")
    with HTTPServer((host, port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.")

if __name__ == "__main__":
    run()
