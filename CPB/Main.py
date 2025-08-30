# Title: TA V – CPB Sensor Node + Cloud-LED-Steürung (BLE-UART Bridge)
# Author: Fabio Panteghini
# Date: 2025-08-30
# -----------------------------------------------------------------
#
# Zweck:
#  - Mind. 2 Umweltsensoren (hier: Licht, Temperatur, Bewegung) erfassen
#  - Werte als Zeitreihe über BLE-UART an den Raspberry Pi senden
#  - NeoPixel per Cloud -> Pi -> BLE-Textkommandos fernsteuern
#
# Datenfluss:
#  CPB (BLE UART Peripheral) <-> Raspberry Pi (BLE Central/Bridge) <-> IoT-Cloud (z. B. Adafruit IO)
#
# Sendeformat (standard 1 Hz):
#  SENS,seq=<n>,ms=<t>,light_raw=<int>,light_f=<float>,temp_C=<float>,ax_ms2=<f>,ay_ms2=<f>,az_ms2=<f>,ax_f=<f>,ay_f=<f>,az_f=<f>,batt_mV=<int>
#
# Empfangskommandos (Text, CSV):
#  FILL,<r>,<g>,<b>
#  PIX,<index>,<r>,<g>,<b>
#  BRI,<0-100>
#  RATE,<Hz>        (0.2 .. 5.0)
#  SELFTEST?        (Prüft Sensoren und 1 Pixel)
#  PING             (liefert OK,PING,ms=<now_ms>)
#  INFO?            (einmalige Infozeile)
#
# Abhängigkeiten (CircuitPython Bundle):
#  - adafruit_ble
#  - adafruit_lis3dh
#  - adafruit_thermistor
#  - neopixel
#
# Board: Adafruit Circuit Playground Bluefruit
# Python: CircuitPython

import time
import board
import neopixel
import analogio
import busio

import adafruit_thermistor
import adafruit_lis3dh

from adafruit_ble import BLERadio
from adafruit_ble.advertising.standard import ProvideServicesAdvertisement
from adafruit_ble.services.nordic import UARTService

# ---------- Konfiguration ----------
NUM_PIXELS = 10
DEFAULT_BRIGHTNESS = 0.2
SENS_INTERVAL = 1.0  # s, Sendeintervall für Sensordaten (per RATE änderbar)
MIN_HZ, MAX_HZ = 0.2, 5.0

ACC_RANGE = adafruit_lis3dh.RANGE_2_G

FW_VERSION = "1.1.0"
BUILD_DATE = "2025-08-30"
NODE_ID = "CPB-01"      # eindeutige ID
INCLUDE_BATTERY = True  # falls nicht verfügbar -> wird -1 gesendet

# Glättung (EMA) für schöne Kurven im Screencast
ALPHA = 0.2

# Rate-Limit für Kommandos
MAX_CMDS_PER_SEC = 20

# Heartbeat für Logs
HEARTBEAT_SEC = 30.0

# ---------- NeoPixel Setup ----------
pixels = neopixel.NeoPixel(board.NEOPIXEL, NUM_PIXELS, brightness=DEFAULT_BRIGHTNESS, auto_write=False)
pixels.fill((0, 0, 0))
pixels.show()

# ---------- Sensoren ----------
light = analogio.AnalogIn(board.LIGHT)
therm = adafruit_thermistor.Thermistor(board.TEMPERATURE, 10000, 10000, 25, 3950)

i2c = busio.I2C(board.SCL, board.SDA)
lis = adafruit_lis3dh.LIS3DH_I2C(i2c)  # 0x18 default
lis.range = ACC_RANGE

# Optionale Batteriespannung (Board Abhängig)
try:
    vbat = analogio.AnalogIn(board.VOLTAGE_MONITOR) if INCLUDE_BATTERY else None
except Exception:
    vbat = None

def read_battery_mV():
    if not vbat:
        return -1
    try:
        # CPB Bluefruit: analog -> 0..3.3 V, Spannungsteiler x2
        return int((vbat.value * 3.3 / 65535.0) * 2 * 1000)
    except Exception:
        return -1

# ---------- BLE UART ----------
ble = BLERadio()
uart = UARTService()

advertisement = ProvideServicesAdvertisement(uart)
advertisement.complete_name = "CPB_TA_V"   # <- eindeutiger Name für das Board

# ---------- Optionaler Watchdog ----------
try:
    import microcontroller, watchdog
    micro_wd = microcontroller.watchdog
    micro_wd.timeout = 8.0
    micro_wd.mode = watchdog.WatchDogMode.RESET
except Exception:
    micro_wd = None

def wd_feed():
    try:
        if micro_wd:
            micro_wd.feed()
    except Exception:
        pass

# ---------- Helfer ----------
def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def parse_ints(parts, start_idx, count):
    vals = []
    for i in range(count):
        vals.append(int(parts[start_idx + i]))
    return vals

def send_line(text):
    try:
        uart.write((text + "\n").encode("utf-8"))
    except Exception:
        pass

def ok(tag, extra=""):
    ts = "{:.0f}".format(time.monotonic()*1000.0)
    send_line(f"OK,{tag},ms={ts}{(','+extra) if extra else ''}")

def err(tag, why=""):
    ts = "{:.0f}".format(time.monotonic()*1000.0)
    send_line(f"ERR,{tag},ms={ts}{(','+why) if why else ''}")

def send_info():
    # Einmalige Info sobald verbunden
    send_line(f"INFO,CPB,TA-V,{FW_VERSION},{BUILD_DATE},NODE={NODE_ID},PIX=10,CMDS=FILL|PIX|BRI|RATE|INFO?|SELFTEST?|PING")

def read_sensors():
    # Liest die 3 Sensoren, gibt ein Tupel (lux_raw, temp_c, ax, ay, az) zurück
    try:
        ax, ay, az = lis.acceleration
    except Exception:
        ax, ay, az = 0.0, 0.0, 0.0
    try:
        temp_c = float(therm.temperature)
    except Exception:
        temp_c = 0.0
    try:
        lux_raw = int(light.value)
    except Exception:
        lux_raw = 0
    return lux_raw, temp_c, ax, ay, az

# ---- einfaches Rate-Limit für Befehle ----
_last_cmd_times = []
def allow_command():
    global _last_cmd_times
    now = time.monotonic()
    _last_cmd_times = [t for t in _last_cmd_times if now - t < 1.0]
    if len(_last_cmd_times) >= MAX_CMDS_PER_SEC:
        return False
    _last_cmd_times.append(now)
    return True

# ---------- Command Handler ----------
def handle_command(line: str):
    # Verarbeitet Kommandos, die vom Raspberry Pi (aus der IoT-Cloud) über BLE kommen.
    line = line.strip()
    if not line:
        return
    if not allow_command():
        err("RATE")
        return

    parts = line.split(",")
    cmd = parts[0].upper()

    if cmd == "FILL" and len(parts) == 4:
        try:
            r, g, b = parse_ints(parts, 1, 3)
            r = clamp(r, 0, 255); g = clamp(g, 0, 255); b = clamp(b, 0, 255)
            pixels.fill((r, g, b)); pixels.show()
            ok("FILL")
        except Exception as e:
            err("FILL", str(e))

    elif cmd == "PIX" and len(parts) == 5:
        try:
            idx, r, g, b = parse_ints(parts, 1, 4)
            if 0 <= idx < NUM_PIXELS:
                r = clamp(r, 0, 255); g = clamp(g, 0, 255); b = clamp(b, 0, 255)
                pixels[idx] = (r, g, b); pixels.show()
                ok("PIX")
            else:
                err("PIX", "idx")
        except Exception as e:
            err("PIX", str(e))

    elif cmd == "BRI" and len(parts) == 2:
        try:
            val = clamp(int(parts[1]), 0, 100)
            pixels.brightness = val / 100.0
            pixels.show()
            ok("BRI")
        except Exception as e:
            err("BRI", str(e))

    elif cmd == "RATE" and len(parts) == 2:
        try:
            hz = float(parts[1])
            if hz < MIN_HZ or hz > MAX_HZ:
                err("RATE", "range")
            else:
                global SENS_INTERVAL
                SENS_INTERVAL = 1.0 / hz
                ok("RATE", f"Hz={hz:.2f}")
        except Exception as e:
            err("RATE", str(e))

    elif cmd in ("SELFTEST","SELFTEST?"):
        ok_sensors = True
        try:
            _ = read_sensors()
        except Exception:
            ok_sensors = False
        try:
            pixels[0] = (10,0,0); pixels.show()
            pixels[0] = (0,0,0); pixels.show()
            ok_led = True
        except Exception:
            ok_led = False
        status = "OK" if (ok_sensors and ok_led) else "ERR"
        send_line(f"SELFTEST,{status},sensors={ok_sensors},led={ok_led}")

    elif cmd in ("INFO", "INFO?"):
        send_info()

    elif cmd == "PING":
        ok("PING")

    else:
        # Unbekanntes Kommando
        err("CMD")

# ---------- Main ----------
buffer = b""
last_sens = 0.0
_last_hb = 0.0
seq = 0

# EMA-Zustände
_f_light = None
_f_ax = _f_ay = _f_az = None

while True:
    # 1. Werbung senden, solange kein Gerät verbunden ist
    ble.start_advertising(advertisement)
    while not ble.connected:
        wd_feed()
        time.sleep(0.05)
    ble.stop_advertising()

    # 2. Sobald verbunden, einmalige Info senden
    send_info()
    last_sens = 0.0
    _last_hb = 0.0

    while ble.connected:
        wd_feed()
        # 3. Eingehende Nachrichten zeilenweise lesen und auswerten
        if uart.in_waiting:
            try:
                data = uart.read(uart.in_waiting)
                if data:
                    buffer += data
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        try:
                            text = line.decode("utf-8").strip()
                        except UnicodeError:
                            continue
                        handle_command(text)
            except Exception:
                # Robust weiterlaufen
                pass

        # 4. Alle SENS_INTERVAL Sekunden aktuelle Sensorwerte senden
        now = time.monotonic()
        if now - last_sens >= SENS_INTERVAL:
            last_sens = now
            lux_raw, temp_c, ax, ay, az = read_sensors()

            # Glättung
            def ema(prev, new, a=ALPHA):
                return new if prev is None else (a*new + (1-a)*prev)
            _f_light = ema(_f_light, float(lux_raw))
            _f_ax = ema(_f_ax, ax); _f_ay = ema(_f_ay, ay); _f_az = ema(_f_az, az)

            seq += 1
            batt_mV = read_battery_mV()
            msg = "SENS,seq={:d},ms={:.0f},light_raw={},light_f={:.1f},temp_C={:.2f},ax_ms2={:.3f},ay_ms2={:.3f},az_ms2={:.3f},ax_f={:.3f},ay_f={:.3f},az_f={:.3f},batt_mV={:d}".format(
                seq, now * 1000.0, lux_raw, _f_light if _f_light is not None else float(lux_raw), temp_c,
                ax, ay, az, _f_ax if _f_ax is not None else ax, _f_ay if _f_ay is not None else ay, _f_az if _f_az is not None else az, batt_mV
            )
            send_line(msg)

        # 5. Heartbeat für Logs
        if now - _last_hb >= HEARTBEAT_SEC:
            _last_hb = now
            send_line(f"HB,ms={now*1000:.0f},node={NODE_ID},ver={FW_VERSION}")

        time.sleep(0.01)