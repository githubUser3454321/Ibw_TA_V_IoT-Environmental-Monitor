#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio, sys, signal, json, re
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

API_BASE = "http://localhost:8123"
API_TELEMETRY = f"{API_BASE}/telemetry"

# --- Parser: toleranter ---
# Erfasst temp / t / temperatureC sowie x/y/z, egal ob ":" oder "=" oder Leerzeichen.
_TOKEN_RE = re.compile(r"(temperaturec|temperature|temp|t|x|y|z)\s*[:=\s]\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def parse_line(line: str, last: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Akzeptiert:
      - JSON: {"temperatureC":..,"axes":{"x":..,"y":..,"z":..}} oder {"temp":..,"x":..,"y":..,"z":..}
      - Tokens: 'T=37.2 X=10 Y=75 Z=1.8' oder 'temp:37.2 x:10 y:75 z:1.8' oder 't 37.2 x 10 y 75 z 1.8'
    Fehlende Werte werden mit 'last' aufgefüllt (Partial-Updates erlaubt).
    """
    line = (line or "").strip()
    if not line:
        return None

    # 1) JSON
    if line.startswith("{") and line.endswith("}"):
        try:
            raw = json.loads(line)
            t = raw.get("temperatureC") or raw.get("temp") or raw.get("t")
            axes = raw.get("axes") or {}
            x = axes.get("x", raw.get("x"))
            y = axes.get("y", raw.get("y"))
            z = axes.get("z", raw.get("z"))
            ts = raw.get("timestamp") or _now_iso()
            # partials auffüllen
            t = float(t) if t is not None else last["temperatureC"]
            x = float(x) if x is not None else last["axes"]["x"]
            y = float(y) if y is not None else last["axes"]["y"]
            z = float(z) if z is not None else last["axes"]["z"]
            return _finalize_state(t, x, y, z, ts)
        except Exception:
            pass

    # 2) Tokens
    tokens = dict((k.lower(), float(v)) for k, v in _TOKEN_RE.findall(line))
    if tokens:
        t = tokens.get("temperaturec") or tokens.get("temperature") or tokens.get("temp") or tokens.get("t")
        x = tokens.get("x")
        y = tokens.get("y")
        z = tokens.get("z")
        # partials auffüllen
        t = t if t is not None else last["temperatureC"]
        x = x if x is not None else last["axes"]["x"]
        y = y if y is not None else last["axes"]["y"]
        z = z if z is not None else last["axes"]["z"]
        return _finalize_state(t, x, y, z, _now_iso())

    # nichts parsebar
    return None

def _finalize_state(t, x, y, z, ts):
    t = _clamp(float(t), -20.0, 60.0)
    x = _clamp(float(x), -180.0, 180.0)
    y = _clamp(float(y), 0.0, 180.0)
    z = _clamp(float(z), 0.4, 5.0)
    return {"temperatureC": t, "axes": {"x": x, "y": y, "z": z}, "timestamp": ts}

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

# --- PUT-Worker mit sichtbarem Logging ---
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
                    print(f"[PUT→API] {payload}")  # ### Debug
                    async with self.session.put(self.url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        txt = await resp.text()
                        print(f"[API←PUT] {resp.status} {txt[:200]}")  # ### Debug
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

        # ### Merker für Partial-Updates
        current = {"temperatureC": 20.0, "axes": {"x": 0.0, "y": 75.0, "z": 2.0}}

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
                    loop = asyncio.get_running_loop()  # ### wichtig

                    async def handle_parsed(line: str):
                        nonlocal current
                        st = parse_line(line, current)
                        if st:
                            current = {"temperatureC": st["temperatureC"], "axes": dict(st["axes"])}
                            await put_worker.submit(st)
                        else:
                            print(f"[Parser] ignoriert: {line}")

                    def handle_notify(_handle, data: bytes):
                        for line in assembler.feed(data):
                            print(f"<< {line}")
                            # ### sicherer Task-Start
                            loop.create_task(handle_parsed(line))

                    await client.start_notify(UART_TX_CHAR_UUID, handle_notify)

                    # Optional: Konsole -> CPB
                    tx_q: asyncio.Queue[str] = asyncio.Queue()
                    stdin_task = asyncio.create_task(stdin_to_queue(tx_q))
                    print("Bereit. Beenden mit Ctrl+C")

                    # ### (Optional) SIM-Test: alle 5s eine Beispiel-Zeile parsen
                    # async def sim():
                    #     while client.is_connected and not stop_event.is_set():
                    #         await asyncio.sleep(5)
                    #         await handle_parsed('temp=42.5 x=30 y=60 z=1.4')
                    # loop.create_task(sim())

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
