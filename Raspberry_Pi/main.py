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
from bleak import BleakClient, BleakScanner, BleakError

# Nordic UART Service (NUS) UUIDs
UART_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
UART_RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Write (Pi -> CPB)
UART_TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # Notify (CPB -> Pi)

# Der in deinem CPB-Code gesetzte Anzeigename:
TARGET_NAME = "CPB_TA_V"

# -------- Helfer --------
class LineAssembler:
#    """Puffert Bytes bis '\n' und gibt komplette UTF-8-Zeilen zurueck."""
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
    print("Suche nach CPB (Name: %s)..." % TARGET_NAME)
    devices = await BleakScanner.discover(timeout=5.0)
    for d in devices:
        # Match per Name ODER per Service UUID (robuster)
        if (d.name == TARGET_NAME) or (UART_SERVICE_UUID.lower() in [s.lower() for s in d.metadata.get("uuids", [])]):
            print(f"Gefunden: {d.name} [{d.address}]")
            return d
    return None

async def stdin_to_queue(queue: asyncio.Queue):
#    """Liest Zeilen von stdin und legt sie in eine Queue (ohne \n)."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    while True:
        line = await reader.readline()
        if not line:
            await asyncio.sleep(0.05)
            continue
        msg = line.decode("utf-8", errors="ignore").strip()
        await queue.put(msg)

async def run():
    stop_event = asyncio.Event()

    def _handle_sigint(*_):
        print("\nBeende...")
        stop_event.set()
    signal.signal(signal.SIGINT, _handle_sigint)

    # Wiederverbinden-Loop
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
                print("Verbunden. Services werden geprueft")

                # Pruefe, ob der NUS-Service vorhanden ist
                svcs = await client.get_services()
                if UART_SERVICE_UUID.lower() not in [s.uuid.lower() for s in svcs]:
                    print("Warnung: NUS-Service nicht gefunden – falsches Geraet")
                else:
                    print("NUS erkannt. Starte Notification-Listener.")

                assembler = LineAssembler()

                def handle_notify(_handle, data: bytes):
                    for line in assembler.feed(data):
                        print(f"<< {line}")

                await client.start_notify(UART_TX_CHAR_UUID, handle_notify)

                # Eingaben aus der Konsole -> an CPB senden
                tx_queue: asyncio.Queue[str] = asyncio.Queue()
                stdin_task = asyncio.create_task(stdin_to_queue(tx_queue))

                print("Bereit. Tipp' Befehle (z.B. 'PING' oder 'INFO') und ENTER.\n"
                      "Zum Beenden: Ctrl+C")
                # Haupt-Loop, bis getrennt oder Ctrl+C
                while client.is_connected and not stop_event.is_set():
                    try:
                        # Falls der Nutzer etwas eingegeben hat: senden
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

                # Aufraeumen
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

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
