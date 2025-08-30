# Title: TA V – CPB Sensor Node + Cloud-LED-Steuerung (BLE-UART Bridge)
# Author: Fabio Panteghini
# Date: 2025-08-30
# -----------------------------------------------------------------
#
# Zweck:
#  - Mind. 2 Umweltsensoren (hier: Licht, Temperatur, Bewegung) erfassen
#  - Werte als Zeitreihe ueber BLE-UART an den Raspberry Pi senden
#  - NeoPixel per Cloud -> Pi -> BLE-Textkommandos fernsteuern
#
# Datenfluss:
#  CPB (BLE UART Peripheral) <-> Raspberry Pi (BLE Central/Bridge) <-> IoT-Cloud (z. B. Adafruit IO)
#
# Sendeformat (1 Hz):
#  SENS,<ms>,<light_raw>,<temp_C>,<ax>,<ay>,<az>\n
#
# Empfangskommandos (Text, CSV):
#  FILL,<r>,<g>,<b>
#  PIX,<index>,<r>,<g>,<b>
#  BRI,<0-100>
#  INFO?   -> CPB sendet einmalige Infozeile
#
# Abhängigkeiten:
#  - adafruit_ble
#  - adafruit_lis3dh
#  - adafruit_thermistor
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
SENS_INTERVAL = 1.0  # s, Sendeintervall fuer Sensordaten
ACC_RANGE = adafruit_lis3dh.RANGE_2_G

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

# ---------- BLE UART ----------
ble = BLERadio()
uart = UARTService()

advertisement = ProvideServicesAdvertisement(uart)
advertisement.complete_name = "CPB_TA_V"   # <- eindeutiger Name für das Board -> Auf dem Pi nach diesem Namen filtern


# ---------- Classes ----------
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

def send_info():
    # Einmalige Info sobald verbunden
    send_line("INFO,CPB,TA-V,v1,PIX=10,CMDS=FILL|PIX|BRI|INFO?")

def read_sensors(): 
    # Liest die 3 Sensoren, gibt ein Tupel (lux_raw, temp_c, ax, ay, az) zurück
    # Licht (raw 0..65535), Temperatur (C), Beschleunigung (m/s^2)
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

def handle_command(line: str):
    # Verarbeitet Kommandos, die vom Raspberry Pi (aus der IoT-Cloud) über BLE kommen.
    # Unterstützte Befehle:
    # - FILL,r,g,b : alle NeoPixel auf Farbe setzen
    # - PIX,i,r,g,b: einzelnes NeoPixel ansteuern
    # - BRI,0-100  : Helligkeit einstellen
    # - INFO?      : Infozeile mit Boarddaten zurücksenden
    line = line.strip()
    if not line:
        return
    parts = line.split(",")
    cmd = parts[0].upper()

    if cmd == "FILL" and len(parts) == 4:
        try:
            r, g, b = parse_ints(parts, 1, 3)
            r = clamp(r, 0, 255); g = clamp(g, 0, 255); b = clamp(b, 0, 255)
            pixels.fill((r, g, b)); pixels.show()
            send_line("OK,FILL")
        except Exception as e:
            send_line("ERR,FILL," + str(e))

    elif cmd == "PIX" and len(parts) == 5:
        try:
            idx, r, g, b = parse_ints(parts, 1, 4)
            if 0 <= idx < NUM_PIXELS:
                r = clamp(r, 0, 255); g = clamp(g, 0, 255); b = clamp(b, 0, 255)
                pixels[idx] = (r, g, b); pixels.show()
                send_line("OK,PIX")
            else:
                send_line("ERR,PIX,idx")
        except Exception as e:
            send_line("ERR,PIX," + str(e))

    elif cmd == "BRI" and len(parts) == 2:
        try:
            val = clamp(int(parts[1]), 0, 100)
            pixels.brightness = val / 100.0
            pixels.show()
            send_line("OK,BRI")
        except Exception as e:
            send_line("ERR,BRI," + str(e))

    elif cmd in ("INFO", "INFO?"):
        send_info()

    else:
        # Unbekanntes Kommando ignorieren
        send_line("ERR,CMD")

# ---------- Main ----------
buffer = b""
last_sens = 0.0

while True:
    # 1. Werbung senden, solange kein Gerät verbunden ist
    ble.start_advertising(advertisement)
    while not ble.connected:
        time.sleep(0.05)
    ble.stop_advertising()

     # 2. Sobald verbunden, einmalige Info senden
    send_info()
    last_sens = 0.0

    while ble.connected:
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
            msg = "SENS,{:.0f},{},{:.2f},{:.3f},{:.3f},{:.3f}".format(
                now * 1000.0, lux_raw, temp_c, ax, ay, az
            )
            send_line(msg)

        time.sleep(0.01)
