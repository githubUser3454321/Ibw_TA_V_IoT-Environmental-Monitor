#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio, sys, signal, json, re, math
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from bleak import BleakClient, BleakScanner, BleakError
import aiohttp

# --- Windows: Bleak mag den Selector-Loop ---
if sys.platform.startswith("win"):
    try:
        import asyncio as _asyncio
        _asyncio.set_event_loop_policy(_asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

# Nordic UART Service (NUS) UUIDs
UART_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
UART_RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Write (Pi -> CPB)
UART_TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # Notify (CPB -> Pi)
TARGET_NAME = "CPB_TA_V"

# ---- Baseline (relative Orientierung beim Start) ----
YAW0_DEG   = 0.0    # X
PITCH0_DEG = 75.0   # Y

# API-Konfiguration
API_BASE = "http://localhost:8123"
API_TELEMETRY = f"{API_BASE}/telemetry"

# --- Parser: Tokens (optional, selten gebraucht)
_TOKEN_RE = re.compile(
    r"(temperaturec|temperature|temp|t|x|y|z)\s*[:=\s]\s*([-+]?\d+(?:\.\d+)?)",
    re.IGNORECASE
)

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _parse_kv_csv(line: str) -> Dict[str, str]:
    parts = line.split(",")
    if parts and "=" not in parts[0]:
        parts = parts[1:]
    out: Dict[str, str] = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out

def _to_float_or_none(v: Optional[str]) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None

# ---- Orientierung aus Beschleunigung ----
def _pitch_from_acc(ax: float, ay: float, az: float) -> float:
    # ax, ay, az in m/s²; Pitch = Rotation um Y
    return math.degrees(math.atan2(-ax, math.sqrt(ay*ay + az*az)))

def _g_norm(ax: float, ay: float, az: float) -> float:
    g = math.sqrt(ax*ax + ay*ay + az*az)
    return g / 9.80665 if 9.80665 else g

# ---- Dein gewünschtes Output-Format (unverändert) ----
def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def _finalize_state(t, x, y, z, ts):
    t = _clamp(float(t), -20.0, 60.0)
    x = _clamp(float(x), -180.0, 180.0)
    y = _clamp(float(y), 0.0, 180.0)
    z = _clamp(float(z), 0.4, 5.0)
    return {"temperatureC": t, "axes": {"x": x, "y": y, "z": z}, "timestamp": ts}

# ---- parse_line: CPB SENS, JSON, Tokens ----
def parse_line(line: str, last: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    line = (line or "").strip()
    if not line:
        return None

    # 1) CPB SENS-CSV
    if line.startswith("SENS"):
        kv = _parse_kv_csv(line)
        t  = (_to_float_or_none(kv.get("temp_c"))
              or _to_float_or_none(kv.get("temperaturec"))
              or _to_float_or_none(kv.get("temp"))
              or _to_float_or_none(kv.get("t")))
        ax = (_to_float_or_none(kv.get("ax_f"))
              or _to_float_or_none(kv.get("ax_ms2"))
              or _to_float_or_none(kv.get("ax")))
        ay = (_to_float_or_none(kv.get("ay_f"))
              or _to_float_or_none(kv.get("ay_ms2"))
              or _to_float_or_none(kv.get("ay")))
        az = (_to_float_or_none(kv.get("az_f"))
              or _to_float_or_none(kv.get("az_ms2"))
              or _to_float_or_none(kv.get("az")))
        ts = _now_iso()

        # Partials auffüllen
        t  = t  if t  is not None else last["temperatureC"]
        ax = ax if ax is not None else last["axes"]["x"]
        ay = ay if ay is not None else last["axes"]["y"]
        az = az if az is not None else last["axes"]["z"]

        # Orientierung relativ zur Baseline
        pitch_deg = _pitch_from_acc(ax, ay, az)
        yaw_rel   = 0.0 - YAW0_DEG              # ohne Magnetometer bleibt yaw ~ 0
        pitch_rel = abs(pitch_deg - PITCH0_DEG) # Abweichung in Grad (0..180)
        gmag      = _g_norm(ax, ay, az)         # z: Gesamtbeschl. in g

        return _finalize_state(t, yaw_rel, pitch_rel, gmag, ts)

    # 2) JSON
    if line.startswith("{") and line.endswith("}"):
        try:
            raw = json.loads(line)
            t  = raw.get("temperatureC") or raw.get("temp") or raw.get("t")
            ax = (raw.get("axes") or {}).get("x", raw.get("x"))
            ay = (raw.get("axes") or {}).get("y", raw.get("y"))
            az = (raw.get("axes") or {}).get("z", raw.get("z"))
            ts = raw.get("timestamp") or _now_iso()

            t  = float(t)  if t  is not None else last["temperatureC"]
            ax = float(ax) if ax is not None else last["axes"]["x"]
            ay = float(ay) if ay is not None else last["axes"]["y"]
            az = float(az) if az is not None else last["axes"]["z"]

            pitch_deg = _pitch_from_acc(ax, ay, az)
            yaw_rel   = 0.0 - YAW0_DEG
            pitch_rel = abs(pitch_deg - PITCH0_DEG)
            gmag      = _g_norm(ax, ay, az)

            return _finalize_state(t, yaw_rel, pitch_rel, gmag, ts)
        except Exception:
            pass

    # 3) Tokens
    tokens = dict((k.lower(), float(v)) for k, v in _TOKEN_RE.findall(line))
    if tokens:
        t  = tokens.get("temperaturec") or tokens.get("temperature") or tokens.get("temp") or tokens.get("t")
        ax = tokens.get("x"); ay = tokens.get("y"); az = tokens.get("z")
        t  = t  if t  is not None else last["temperatureC"]
        ax = ax if ax is not None else last["axes"]["x"]
        ay = ay if ay is not None else last["axes"]["y"]
        az = az if az is not None else last["axes"]["z"]

        pitch_deg = _pitch_from_acc(ax, ay, az)
        yaw_rel   = 0.0 - YAW0_DEG
        pitch_rel = abs(pitch_deg - PITCH0_DEG)
        gmag      = _g_norm(ax, ay, az)

        return _finalize_state(t, yaw_rel, pitch_rel, gmag, _now_iso())

    return None

# ---- BLE: Zeilenassembler & Loop ----
class LineAssembler:
    def __init__(self): self._buf = bytearray()
    def feed(self, data: bytes):
        out = []
        self._buf.extend(data)
        while True:
            try:
                i = self._buf.index(0x0A)  # \n
            except ValueError:
                break
            chunk = self._buf[:i]; del self._buf[:i+1]
            try: out.append(chunk.decode("utf-8", "ignore").rstrip("\r"))
            except UnicodeDecodeError: pass
        return out

async def find_device():
    print(f"Suche nach CPB (Name: {TARGET_NAME})…")
    devs = await BleakScanner.discover(timeout=5.0)
    for d in devs:
        if (d.name == TARGET_NAME) or (UART_SERVICE_UUID.lower() in [s.lower() for s in d.metadata.get("uuids", [])]):
            print(f"Gefunden: {d.name} [{d.address}]")
            return d
    return None

async def stdin_to_queue(q: asyncio.Queue):
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    proto = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: proto, sys.stdin)
    while True:
        line = await reader.readline()
        if not line:
            await asyncio.sleep(0.05); continue
        await q.put(line.decode("utf-8", "ignore").strip())

# --- PUT-Worker ---
class PutWorker:
    def __init__(self, session: aiohttp.ClientSession, url: str, max_rate_hz: float = 10.0):
        self.session = session
        self.url = url
        self.latest: Optional[Dict[str, Any]] = None
        self.min_interval = 1.0 / max_rate_hz
        self._task = asyncio.create_task(self._run())

    async def submit(self, state: Dict[str, Any]):
        self.latest = state

    async def _run(self):
        try:
            while True:
                await asyncio.sleep(self.min_interval)
                if not self.latest:
                    continue
                payload = self.latest
                self.latest = None
                try:
                    print(f"[PUT→API] {payload}")
                    async with self.session.put(self.url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        txt = await resp.text()
                        print(f"[API←PUT] {resp.status} {txt[:200]}")
                except Exception as e:
                    print(f"[PUT] Fehler: {e}")
        except asyncio.CancelledError:
            pass

    async def close(self):
        self._task.cancel()
        try: await self._task
        except asyncio.CancelledError: pass

async def run():
    stop_event = asyncio.Event()
    def _sigint(*_): print("\nBeende…"); stop_event.set()
    signal.signal(signal.SIGINT, _sigint)

    async with aiohttp.ClientSession() as http:
        put_worker = PutWorker(http, API_TELEMETRY, max_rate_hz=10.0)

        # RAM-State
        current = {"temperatureC": 20.0, "axes": {"x": 0.0, "y": 0.0, "z": 9.81}}

        while not stop_event.is_set():
            try:
                dev = await find_device()
                if not dev:
                    print("Kein CPB gefunden. Erneuter Versuch in 3s…")
                    try: await asyncio.wait_for(stop_event.wait(), timeout=3.0)
                    except asyncio.TimeoutError: continue
                    break

                async with BleakClient(dev) as client:
                    if not client.is_connected:
                        print("Konnte nicht verbinden."); continue
                    print("Verbunden. Services werden geprüft…")
                    svcs = await client.get_services()
                    if UART_SERVICE_UUID.lower() not in [s.uuid.lower() for s in svcs]:
                        print("Warnung: NUS-Service nicht gefunden – falsches Gerät")
                    else:
                        print("NUS erkannt. Starte Notifications.")

                    assembler = LineAssembler()
                    loop = asyncio.get_running_loop()

                    async def handle_parsed(line: str):
                        nonlocal current
                        st = parse_line(line, current)
                        if not st:
                            if line.strip():
                                print(f"[Parser] ignoriert: {line}")
                            return
                        current = {"temperatureC": st["temperatureC"], "axes": dict(st["axes"])}
                        print(st)
                        await put_worker.submit(st)

                    def handle_notify(_handle, data: bytes):
                        for line in assembler.feed(data):
                            loop.create_task(handle_parsed(line))

                    await client.start_notify(UART_TX_CHAR_UUID, handle_notify)

                    tx_q: asyncio.Queue[str] = asyncio.Queue()
                    stdin_task = asyncio.create_task(stdin_to_queue(tx_q))
                    print("Bereit. Beenden mit Ctrl+C")

                    while client.is_connected and not stop_event.is_set():
                        try:
                            try:
                                cmd = await asyncio.wait_for(tx_q.get(), timeout=0.2)
                                if cmd:
                                    await client.write_gatt_char(UART_RX_CHAR_UUID, (cmd+"\n").encode("utf-8"), response=False)
                                    print(f">> {cmd}")
                            except asyncio.TimeoutError:
                                pass
                        except BleakError as e:
                            print("BLE-Fehler:", e); break
                        except Exception as e:
                            print("Fehler:", e); break

                    try: await client.stop_notify(UART_TX_CHAR_UUID)
                    except Exception: pass
                    stdin_task.cancel()

            except BleakError as e:
                print("Verbindungsfehler:", e)
            except Exception as e:
                print("Unerwarteter Fehler:", e)

            if not stop_event.is_set():
                print("Getrennt. Neuer Verbindungsversuch in 3s…")
                try: await asyncio.wait_for(stop_event.wait(), timeout=3.0)
                except asyncio.TimeoutError: pass

        await put_worker.close()

if __name__ == "__main__":
    try: asyncio.run(run())
    except KeyboardInterrupt: pass
