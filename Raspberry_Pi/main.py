#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio, sys, signal, json, re, math
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from bleak import BleakClient, BleakScanner, BleakError
import aiohttp
import contextlib

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
API_LED = f"{API_BASE}/led"


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

# ---- Dein gewünschtes Output-Format (unverändert) ----
def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def _finalize_state(t, light_raw, light_norm, ts):
    t = _clamp(float(t), -20.0, 60.0)
    light_raw = max(0, int(light_raw))
    light_norm = _clamp(float(light_norm), 0.0, 1.0)
    return {"temperatureC": t, "light": {"raw": light_raw, "norm": light_norm}, "timestamp": ts}


# ---- parse_line: CPB SENS, JSON, Tokens ----
def parse_line(line: str, last: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    line = (line or "").strip()
    if not line:
        return None
    ts = _now_iso()

    # 1) CPB SENS-CSV (neu)
    if line.startswith("SENS"):
        kv = _parse_kv_csv(line)
        t  = (_to_float_or_none(kv.get("temp_c"))
              or _to_float_or_none(kv.get("temperaturec"))
              or _to_float_or_none(kv.get("temp"))
              or _to_float_or_none(kv.get("t")))
        lr = _to_float_or_none(kv.get("light_raw"))
        ln = _to_float_or_none(kv.get("light_norm"))

        # Partials: aus letztem State auffüllen
        t  = t  if t  is not None else last.get("temperatureC", 20.0)
        lr = lr if lr is not None else ((last.get("light") or {}).get("raw", 0))
        ln = ln if ln is not None else ((last.get("light") or {}).get("norm", 0.0))

        return _finalize_state(t, lr, ln, ts)
    if line.startswith("{") and line.endswith("}"):
        try:
            raw = json.loads(line)
            t  = raw.get("temperatureC") or raw.get("temp") or raw.get("t")
            light = raw.get("light") or {}
            lr = light.get("raw", raw.get("light_raw"))
            ln = light.get("norm", raw.get("light_norm"))

            t  = float(t)  if t  is not None else last.get("temperatureC", 20.0)
            lr = float(lr) if lr is not None else ((last.get("light") or {}).get("raw", 0))
            ln = float(ln) if ln is not None else ((last.get("light") or {}).get("norm", 0.0))

            return _finalize_state(t, lr, ln, raw.get("timestamp") or ts)
        except Exception:
            pass

    # 3) Sonst ignorieren
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


class LedWorker:
    def __init__(self, http: aiohttp.ClientSession, client_write):
        self.http = http
        self.client_write = client_write  # async fn(data: bytes) -> None
        self.last_applied = None
        self.task = asyncio.create_task(self._run())

    async def _fetch_desired(self):
        try:
            async with self.http.get(API_LED, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                if resp.status != 200: return None
                return await resp.json()
        except Exception:
            return None

    async def _apply(self, desire):
        # desire: {"on":bool,"rgb":[r,g,b],"brightness":0..100}
        if not desire: return
        if desire == self.last_applied: return  # nichts zu tun

        if desire["on"]:
            # Reihenfolge: Brightness dann Fill
            bri = int(desire.get("brightness", 20))
            r, g, b = map(int, desire.get("rgb", [255,160,0]))
            await self.client_write(f"BRIGHT {bri}\n".encode("utf-8"))
            await self.client_write(f"FILL {r} {g} {b}\n".encode("utf-8"))
        else:
            await self.client_write(b"OFF\n")

        self.last_applied = dict(desire)

    async def _run(self):
        try:
            while True:
                desire = await self._fetch_desired()
                await self._apply(desire)
                await asyncio.sleep(0.3)  # 300 ms Polling
        except asyncio.CancelledError:
            pass

    async def close(self):
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task



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
        current = {"temperatureC": 20.0, "light": {"raw": 0, "norm": 0.0}}

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
                        current = {"temperatureC": st["temperatureC"], "light": dict(st["light"])}
                        print(st)
                        await put_worker.submit(st)

                    def handle_notify(_handle, data: bytes):
                        for line in assembler.feed(data):
                            loop.create_task(handle_parsed(line))

                    await client.start_notify(UART_TX_CHAR_UUID, handle_notify)
                    async def _client_write(data: bytes):
                        await client.write_gatt_char(UART_RX_CHAR_UUID, data, response=False)

                    led_worker = LedWorker(http, _client_write) 

                    tx_q: asyncio.Queue[str] = asyncio.Queue()
                    stdin_task = asyncio.create_task(stdin_to_queue(tx_q))
                    print("Bereit. Beenden mit Ctrl+C")
                    try:
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
                    finally:
                        with contextlib.suppress(Exception):
                            await client.stop_notify(UART_TX_CHAR_UUID)
                        
                        stdin_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await stdin_task

                    await led_worker.close()

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
