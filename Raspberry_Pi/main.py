#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#"""
#Raspberry Pi BLE Central fuer Adafruit CPB (BLE UART):
# - Verbindet sich mit dem CPB (Advertise-Name: CPB_TA_V)
# - Abonniert NUS-Notifications und druckt empfangene Textzeilen
# - Optional: Eingaben aus der Konsole werden als Befehle an den CPB gesendet
#"""

import asyncio
import sys
import signal
import json
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from bleak import BleakClient, BleakScanner, BleakError
import aiohttp

# Nordic UART Service (NUS) UUIDs
UART_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
UART_RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Write (Pi -> CPB)
UART_TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # Notify (CPB -> Pi)
# Der in deinem CPB-Code gesetzte Anzeigename:
TARGET_NAME = "CPB_TA_V"

# API Destination
API_BASE = "http://localhost:8123"
API_TELEMETRY = f"{API_BASE}/telemetry"

# API Parser
_KV_RE = re.compile(r"([A-Za-z_]+)\s*[:=]\s*([-+]?\d+\.?\d*)")




def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def _norm_keys(d: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k).strip().lower(): v for k, v in d.items()}

def parse_line(line: str) -> Optional[Dict[str, Any]]:
    """
    Versucht mehrere Formate:
      1) JSON mit {temperatureC,temp,t} und {axes:{x,y,z}} oder x,y,z top-level
      2) Key=Value/Colon: T=.., Temp:.., X:.., Y:.., Z:.., (optional timestamp)
    Gibt dict im Serverformat zurück: {"temperatureC": float, "axes": {"x":..,"y":..,"z":..}, "timestamp": "..."}
    oder None (wenn nicht parsebar).
    """
    line = line.strip()
    if not line:
        return None

    # 1) JSON
    if line.startswith("{") and line.endswith("}"):
        try:
            raw = json.loads(line)
            rawN = _norm_keys(raw)
            out = {"temperatureC": None, "axes": {"x": None, "y": None, "z": None}, "timestamp": raw.get("timestamp")}
            # temp
            for key in ("temperaturec", "temp", "t"):
                if key in rawN:
                    out["temperatureC"] = float(rawN[key])
                    break
            # axes
            axes = raw.get("axes")
            if isinstance(axes, dict):
                axesN = _norm_keys(axes)
                for k in ("x", "y", "z"):
                    if k in axesN:
                        out["axes"][k] = float(axesN[k])
            # fallback: top-level x/y/z
            for k in ("x", "y", "z"):
                if out["axes"][k] is None and k in rawN:
                    out["axes"][k] = float(rawN[k])

            # prüfen
            if out["temperatureC"] is None or None in out["axes"].values():
                # unvollständig
                pass
            else:
                return _finalize_state(out)
        except Exception:
            pass

    # 2) Key=Value/Colon
    pairs = dict((m.group(1).lower(), float(m.group(2))) for m in _KV_RE.finditer(line))
    if pairs:
        t = None
        for key in ("temperaturec", "temp", "t"):
            if key in pairs:
                t = pairs[key]
                break
        x = pairs.get("x")
        y = pairs.get("y")
        z = pairs.get("z")
        ts_match = re.search(r"timestamp\s*[:=]\s*([^\s;]+)", line, re.IGNORECASE)
        ts = ts_match.group(1) if ts_match else None
        if t is not None and x is not None and y is not None and z is not None:
            return _finalize_state({"temperatureC": t, "axes": {"x": x, "y": y, "z": z}, "timestamp": ts})

    return None

def _finalize_state(s: Dict[str, Any]) -> Dict[str, Any]:
    t = float(s["temperatureC"])
    x = float(s["axes"]["x"])
    y = float(s["axes"]["y"])
    z = float(s["axes"]["z"])
    ts = s.get("timestamp") or _now_iso()
    # clamp gemäß Server
    t = _clamp(t, -20.0, 60.0)
    x = _clamp(x, -180.0, 180.0)
    y = _clamp(y, 0.0, 180.0)
    z = _clamp(z, 0.4, 5.0)
    return {"temperatureC": t, "axes": {"x": x, "y": y, "z": z}, "timestamp": ts}

# -------- BLE Helfer (wie gehabt) --------
class LineAssembler:
    def __init__(self):
        self._buf = bytearray()
    def feed(self, data: bytes):
        lines = []
        self._buf.extend(data)
        while True:
            try:
                idx = self._buf.index(0x0A)  # \n
            except ValueError:
                break
            chunk = self._buf[:idx]
            del self._buf[:idx+1]
            try:
                lines.append(chunk.decode("utf-8", errors="ignore").rstrip("\r"))
            except UnicodeDecodeError:
                pass
        return lines

async def find_device():
    print(f"Suche nach CPB (Name: {TARGET_NAME})...")
    devices = await BleakScanner.discover(timeout=5.0)
    for d in devices:
        if (d.name == TARGET_NAME) or (UART_SERVICE_UUID.lower() in [s.lower() for s in d.metadata.get("uuids", [])]):
            print(f"Gefunden: {d.name} [{d.address}]")
            return d
    return None

async def stdin_to_queue(queue: asyncio.Queue):
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    while True:
        line = await reader.readline()
        if not line:
            await asyncio.sleep(0.05)
            continue
        await queue.put(line.decode("utf-8", errors="ignore").strip())

# ---------------- PUT-Worker (coalescing) ----------------
class PutWorker:
    def __init__(self, session: aiohttp.ClientSession, url: str, max_rate_hz: float = 10.0):
        self.session = session
        self.url = url
        self.queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self.latest: Optional[Dict[str, Any]] = None
        self.min_interval = 1.0 / max_rate_hz
        self._task = asyncio.create_task(self._run())

    async def submit(self, state: Dict[str, Any]):
        # Nur den neuesten Zustand behalten (coalescing)
        self.latest = state

    async def _run(self):
        last = 0.0
        try:
            while True:
                await asyncio.sleep(self.min_interval)
                if not self.latest:
                    continue
                payload = self.latest
                self.latest = None
                try:
                    async with self.session.put(self.url, json=payload, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            print(f"[PUT] HTTP {resp.status}: {text}")
                except Exception as e:
                    print(f"[PUT] Fehler: {e}")
        except asyncio.CancelledError:
            pass

    async def close(self):
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

# ---------------- Main-Flow ----------------
async def run():
    stop_event = asyncio.Event()

    def _handle_sigint(*_):
        print("\nBeende...")
        stop_event.set()
    signal.signal(signal.SIGINT, _handle_sigint)

    async with aiohttp.ClientSession() as http:
        put_worker = PutWorker(http, API_TELEMETRY, max_rate_hz=10.0)

        while not stop_event.is_set():
            try:
                dev = await find_device()
                if not dev:
                    print("Kein CPB gefunden. Erneuter Versuch in 3s...")
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=3.0)
                    except asyncio.TimeoutError:
                        continue
                    break

                async with BleakClient(dev) as client:
                    if not client.is_connected:
                        print("Konnte nicht verbinden.")
                        continue
                    print("Verbunden. Services werden geprüft")

                    svcs = await client.get_services()
                    if UART_SERVICE_UUID.lower() not in [s.uuid.lower() for s in svcs]:
                        print("Warnung: NUS-Service nicht gefunden – falsches Gerät")
                    else:
                        print("NUS erkannt. Starte Notification-Listener.")

                    assembler = LineAssembler()

                    async def handle_parsed(line: str):
                        st = parse_line(line)
                        if st:
                            await put_worker.submit(st)

                    def handle_notify(_handle, data: bytes):
                        for line in assembler.feed(data):
                            print(f"<< {line}")
                            # fire-and-forget: in den Eventloop einplanen
                            asyncio.get_event_loop().create_task(handle_parsed(line))

                    await client.start_notify(UART_TX_CHAR_UUID, handle_notify)

                    # Optionale Konsole -> CPB (wie gehabt)
                    tx_queue: asyncio.Queue[str] = asyncio.Queue()
                    stdin_task = asyncio.create_task(stdin_to_queue(tx_queue))

                    print("Bereit. Eingaben -> CPB. Beenden mit Ctrl+C")
                    while client.is_connected and not stop_event.is_set():
                        try:
                            try:
                                cmd = await asyncio.wait_for(tx_queue.get(), timeout=0.1)
                                if cmd:
                                    payload = (cmd + "\n").encode("utf-8")
                                    await client.write_gatt_char(UART_RX_CHAR_UUID, payload, response=False)
                                    print(f">> {cmd}")
                            except asyncio.TimeoutError:
                                pass
                        except BleakError as e:
                            print("BLE-Fehler:", e)
                            break
                        except Exception as e:
                            print("Fehler:", e)
                            break

                    try:
                        await client.stop_notify(UART_TX_CHAR_UUID)
                    except Exception:
                        pass
                    stdin_task.cancel()

            except BleakError as e:
                print("Verbindungsfehler:", e)
            except Exception as e:
                print("Unerwarteter Fehler:", e)

            if not stop_event.is_set():
                print("Getrennt. Neuer Verbindungsversuch in 3s...")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    pass

        await put_worker.close()

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass