# Title: TA V – CPB Sensor Node + Cloud-LED-Steuerung (BLE-UART Bridge)
# Author: Fabio Panteghini
# Date: 2025-09-13
# -----------------------------------------------------------------
#
# UEBERSICHT
# Dieses Skript laeuft auf dem Adafruit Circuit Playground Bluefruit (CPB, nRF52840)
# mit CircuitPython. Es bildet einen BLE-UART Sensor-Knoten, der:
#   - Temperatur (Thermistor) und Licht (ALS) erfasst,
#   - die Daten periodisch als Textzeilen (CSV-aehnlich) ueber BLE UART an einen
#     Raspberry Pi (Central/Bridge) sendet,
#   - NeoPixel-LEDs per Textkommandos aus der Cloud/Pi steuern kann.
#
# ARCHITEKTUR / DATENFLUSS
#  CPB (BLE UART Peripheral) <-> Raspberry Pi (BLE Central/Bridge) <-> IoT-Cloud
#
# TELEMETRIE-FRAME (Standard 1 Hz):
#   SENS,ms=<t>,temp_C=<float>,light_raw=<int>,light_norm=<float>
#
# KOMMANDOS (vom Pi/Cloud an CPB, als Textzeilen mit \n)
#   - FILL r g b       : Alle NeoPixel auf RGB (0..255)
#   - FILLHEX RRGGBB   : Alle NeoPixel auf Hexfarbe (z. B. FF8800)
#   - BRIGHT <0..100>  : Helligkeit in Prozent
#   - OFF              : Alle NeoPixel aus
#   - RESET            : Zentraler Resetpfad (STATE_RESET). Macht KEIN Hard-Reboot,
#                        sondern setzt LED- und Laufzeit-Zustand zurueck.
#   - GET / GET?       : Status der NeoPixel (Farben, Helligkeit)
#   - TEMP? / GETTEMP? : Einzelmessung Temperatur
#   - LIGHT?           : Einzelmessung Licht
#   - SENS? / GETSENS? : Eine Telemetriezeile sofort senden
#   - TELEM <sek>      : Telemetrie-Intervall in Sekunden (0 = AUS)
#
# FEHLER-/RESET-STRATEGIE (State Machine)
#   STATES:
#     STATE_WAIT   : Werbeblinken/Advertising bis BLE verbunden
#     STATE_HANDLE : Normalbetrieb: Kommandos verarbeiten, Telemetrie senden
#     STATE_RESET  : Zentraler Resetpfad: LED/Speicher/Timer zuruecksetzen,
#                    je nach Verbindung zurueck nach HANDLE oder WAIT
#     STATE_ERROR  : Optisches Fehlerfeedback, springt sofort nach STATE_RESET
#
#   WICHTIG:
#     - Jeder Fehler (Parser/Top-Level-Exception) wechselt unmittelbar nach STATE_RESET.
#     - Das Kommando RESET setzt NICHT direkt LEDs zurueck, sondern setzt nur den
#       State auf STATE_RESET (saubere, zentrale Ruecksetzung an EINER Stelle).
#     - Im RESET-State wird bei bestehender BLE-Verbindung nach STATE_HANDLE weiter-
#       geschaltet; ist die Verbindung weg, wird Advertising (Warten) gestartet.
#
# ABHAENGIGKEITEN (CircuitPython Bundle):
#   adafruit_ble, adafruit_thermistor, neopixel

import time
import board
import neopixel
import analogio
from adafruit_thermistor import Thermistor

from adafruit_ble import BLERadio
from adafruit_ble.advertising.standard import ProvideServicesAdvertisement
from adafruit_ble.services.nordic import UARTService

# === NeoPixel Setup ===
NUM_PIXELS = 10
# brightness ist global fuer alle Pixel; auto_write=False -> explizites .show() noetig
pixels = neopixel.NeoPixel(board.NEOPIXEL, NUM_PIXELS, brightness=0.06, auto_write=False)

# === Sensoren ===
# Temperatur: NTC am A9 (oder board.TEMPERATURE falls im Board-Def vorhanden)
THERM_PIN = getattr(board, "TEMPERATURE", board.A9)
thermistor = Thermistor(
    pin=THERM_PIN,
    series_resistor=10000.0,
    nominal_resistance=10000.0,
    nominal_temperature=25.0,
    b_coefficient=3380.0
)

# Licht: ALS-PT19 an A8, Rohwert 0..65535
light = analogio.AnalogIn(board.A8)

# === BLE Setup ===
# BLERadio = Funk-Stack; UARTService = "Nordic UART" (seriell ueber BLE)
ble = BLERadio()
uart = UARTService()
adv = ProvideServicesAdvertisement(uart)
adv.complete_name = "CPB_TA_V"
# Peripheral geht sofort ins Advertising (Central/Bridge kann verbinden)
ble.start_advertising(adv)
print("DBG: Advertising gestartet als", adv.complete_name)  # DBG

# === State Machine ===
STATE_WAIT, STATE_HANDLE, STATE_RESET, STATE_ERROR = range(4)
state = STATE_WAIT  # Startzustand: warten/advertising

# LED-Farbpuffer (logischer Zustand) – physisches Schreiben via aktualisiere()
farben = [(40, 40, 40)] * NUM_PIXELS

# Variablen fuer "Warte-Blinken" im WAIT-State
wait_t = time.monotonic()
wait_on = False

# UART-Zeilenpuffer (wir lesen ggf. Stueckweise und rekonstruieren Zeilen bis '\n')
rx_buf = ""

# Telemetrie-Intervall (Sekunden) und letzter Sendezeitpunkt
telemetry_period = 1.0
telemetry_t = time.monotonic()

# === Helper: optisches Fehlerfeedback ===
def blinken_error(n=2, farbe=(50, 0, 0), dauer=0.15):
    """Kurzes rotes Blinken zur Fehleranzeige (nicht-blockierender Reset folgt)."""
    for _ in range(n):
        pixels.fill(farbe)
        pixels.show()
        time.sleep(dauer)
        pixels.fill((0, 0, 0))
        pixels.show()
        time.sleep(dauer)

# === Helper: langsames Blau-Blinken in WAIT ===
def blink_wait():
    """Zeigt an, dass wir warten/advertising (keine BLE-Verbindung)."""
    global wait_t, wait_on
    if time.monotonic() - wait_t > 0.5:
        wait_t = time.monotonic()
        wait_on = not wait_on
        pixels.fill((0, 0, 50) if wait_on else (0, 0, 0))
        pixels.show()

# === Helper: physische LED-Aktualisierung gem. 'farben' Puffer ===
def aktualisiere():
    for i in range(NUM_PIXELS):
        pixels[i] = farben[i]
    pixels.show()

def clamp8(x):
    """Begrenzt auf gueltigen 8-bit RGB-Bereich 0..255."""
    return max(0, min(255, int(x)))

def set_all(r, g, b):
    """Alle NeoPixel auf eine RGB-Farbe setzen (mit Clamp)."""
    rgb = (clamp8(r), clamp8(g), clamp8(b))
    for i in range(NUM_PIXELS):
        farben[i] = rgb
    aktualisiere()

def set_all_hex(hexstr):
    """Hex-String '#RRGGBB' oder 'RRGGBB' in Farbe umsetzen."""
    hs = hexstr.strip().lstrip("#")
    if len(hs) != 6:
        return False
    r = int(hs[0:2], 16)
    g = int(hs[2:4], 16)
    b = int(hs[4:6], 16)
    set_all(r, g, b)
    return True

def reset_all():
    """Standardzustand der NeoPixel + kurze blaue Quittierung."""
    for i in range(NUM_PIXELS):
        farben[i] = (40, 40, 40)   # neutrale Grundfarbe
    pixels.brightness = 0.06       # Standardhelligkeit
    # kurze blaue Quittierung
    pixels.fill((0, 0, 80)); pixels.show(); time.sleep(0.2)
    pixels.fill((0, 0, 0));  pixels.show()
    aktualisiere()                 # finaler Sync aus 'farben'

# === Helper: Antworten ueber BLE UART schicken ===
def ok(msg):
    """Standard-OK-Zeile zuruecksenden (z. B. 'OK RESET')."""
    try:
        uart.write(("OK " + msg + "\n").encode("utf-8"))
    except Exception as e:
        print("UART write err:", e)

def err(msg):
    """Standard-ERR-Zeile zuruecksenden (z. B. 'ERR unknown')."""
    try:
        uart.write(("ERR " + msg + "\n").encode("utf-8"))
    except Exception as e:
        print("UART write err:", e)

def send_status():
    """Aktuellen NeoPixel-Status als eine kompakte Zeile zuruecksenden."""
    try:
        parts = []
        for i, (r, g, b) in enumerate(farben):
            parts.append("{}:{},{},{}".format(i, r, g, b))
        cols = ";".join(parts)
        out = "STAT bright={} colors={}\n".format(int(pixels.brightness*100), cols)
        uart.write(out.encode("utf-8"))
        print("DBG >>", out.strip())  # DBG
    except Exception as e:
        print("UART write err:", e)

def ms():
    """Millis seit Boot (aus monotonic_ns abgeleitet, fuer Telemetrie-Timestamp)."""
    return time.monotonic_ns() // 1_000_000

# === Sensors: Readouts ===
def read_temp_c():
    """Temperaturmessung (float, Grad C). Bei Fehler NaN."""
    try:
        return float(thermistor.temperature)
    except Exception as e:
        print("ERR temp:", e)
        return float("nan")

def read_light():
    """Lichtmessung: Rohwert 0..65535, normiert 0..1.0. Bei Fehler (-1, 0.0)."""
    try:
        raw = int(light.value)
        norm = raw / 65535.0
        return raw, norm
    except Exception as e:
        print("ERR light:", e)
        return -1, 0.0

# === Telemetrie-Zeile senden ===
def send_sens_line():
    """Erstellt und sendet die SENS-Zeile ueber BLE UART (CSV-aehnlich)."""
    t_c = read_temp_c()
    l_raw, l_norm = read_light()
    line = "SENS"
    line += ",ms={}".format(ms())
    line += ",temp_C={:.2f}".format(t_c)
    line += ",light_raw={},light_norm={:.4f}".format(l_raw, l_norm)
    line += "\n"
    try:
        uart.write(line.encode("utf-8"))
        print("DBG >>", line.strip())  # DBG
    except Exception as e:
        print("UART write err:", e)

# === Kommando-Parser ===
def handle_command(line: str):
    """
    Zerlegt eine empfangene Zeile in Token und fuehrt das entsprechende Kommando aus.
    WICHTIG: 'RESET' setzt NICHT direkt LEDs zurueck, sondern setzt state=STATE_RESET,
    sodass die zentrale Resetlogik greift.
    """
    global telemetry_period, state
    if not line:
        return
    print("DBG <<", line)  # DBG: Rohzeile
    parts = line.strip().split()
    if not parts:
        return
    cmd = parts[0].upper()

    try:
        # LED-Befehle
        if cmd == "FILL" and len(parts) == 4:
            r, g, b = map(int, parts[1:4])
            set_all(r, g, b)
            ok("FILL {} {} {}".format(r, g, b))

        elif cmd == "FILLHEX" and len(parts) == 2:
            if set_all_hex(parts[1]):
                ok("FILLHEX " + parts[1])
            else:
                err("hex")

        elif cmd == "BRIGHT" and len(parts) == 2:
            pct = max(0, min(100, int(parts[1])))
            pixels.brightness = pct / 100.0
            aktualisiere()
            ok("BRIGHT {}".format(pct))

        elif cmd == "OFF":
            set_all(0, 0, 0)
            ok("OFF")

        # Zentraler Reset-Trigger aus der Cloud/Bridge
        elif cmd == "RESET":
            ok("RESET")         # Quittung
            state = STATE_RESET # eigentliche Ruecksetzung im RESET-State

        # Status-/Mess-Befehle
        elif cmd in ("GET?", "GET"):
            send_status()

        elif cmd in ("GETTEMP?", "TEMP?"):
            t_c = read_temp_c()
            out = "TEMP C={:.2f}\n".format(t_c)
            try:
                uart.write(out.encode("utf-8"))
                print("DBG >>", out.strip())
            except Exception as e:
                print("UART write err:", e)

        elif cmd in ("GETLIGHT?", "LIGHT?"):
            l_raw, l_norm = read_light()
            out = "LIGHT raw={} norm={:.4f}\n".format(l_raw, l_norm)
            try:
                uart.write(out.encode("utf-8"))
                print("DBG >>", out.strip())
            except Exception as e:
                print("UART write err:", e)

        elif cmd in ("GETSENS?", "SENS?"):
            send_sens_line()

        # Telemetrie-Konfiguration
        elif cmd == "TELEM" and len(parts) == 2:
            val_raw = parts[1].upper()
            if val_raw == "OFF":
                telemetry_period = 0.0
                ok("TELEM OFF")
                print("DBG: Telemetrie AUS")
            else:
                val = float(parts[1])
                if val < 0.0:
                    val = 0.0
                telemetry_period = val
                ok("TELEM {}".format(val))
                print("DBG: Telemetrie Intervall =", telemetry_period, "s")

        else:
            # unbekanntes Kommando -> saubere Fehlermeldung
            err("unknown")

    except Exception as e:
        # Parser-/Kommando-Fehler: anzeigen, zurueckmelden und zentral resetten
        print("Cmd error:", e)
        err("format")
        state = STATE_RESET  # sofort in Reset-State

# === Hauptschleife / Zustandsautomat ===
while True:
    try:
        if state == STATE_WAIT:
            # Anzeige, dass wir im Advertising sind und auf Verbindung warten
            blink_wait()
            if ble.connected:
                # kurze gruen-Blink-Quittung, dann initialer LED-Reset,
                # Puffer leeren, Telemetrie-Timer setzen, in HANDLE uebergehen
                print("DBG: BLE verbunden")
                pixels.fill((0, 50, 0)); pixels.show(); time.sleep(0.2)
                pixels.fill((0, 0, 0));  pixels.show()
                reset_all()
                rx_buf = ""
                telemetry_t = time.monotonic()
                state = STATE_HANDLE

        elif state == STATE_HANDLE:
            # --- Eingehende UART-Daten zeilenweise verarbeiten ---
            if uart.in_waiting:
                raw = uart.read(uart.in_waiting)
                if raw:
                    # KEINE Keyword-Args bei decode()
                    rx_buf += raw.decode("utf-8","ignore")
                    # Es kann mehrere Zeilen in einem Paket geben
                    while "\n" in rx_buf:
                        line, rx_buf = rx_buf.split("\n", 1)
                        handle_command(line.strip())

            # --- Periodische Telemetrie senden ---
            if telemetry_period > 0.0:
                now = time.monotonic()
                if now - telemetry_t >= telemetry_period:
                    telemetry_t = now
                    send_sens_line()

            # --- Verbindungsueberwachung ---
            if not ble.connected:
                print("DBG: BLE getrennt – starte Advertising")
                ble.start_advertising(adv)
                state = STATE_WAIT

        elif state == STATE_RESET:
            # Zentraler Resetpfad: saubere Ruecksetzung EINER Stelle
            print("DBG: STATE_RESET")
            reset_all()                      # LED-/Helligkeit-Reset
            rx_buf = ""                      # Zeilenpuffer leeren
            telemetry_t = time.monotonic()   # Telemetrie-Timer neu setzen

            # Anschlusszustand pruefen und geeigneten Folgezustand waehlen
            if not ble.connected:
                # keine Verbindung -> Advertising + Warten
                ble.start_advertising(adv)
                state = STATE_WAIT
            else:
                # Verbindung steht -> in den Normalbetrieb
                state = STATE_HANDLE

        elif state == STATE_ERROR:
            # Kurzes optisches Feedback, danach IMMER in Reset-State
            print("DBG: STATE_ERROR")
            blinken_error()
            state = STATE_RESET

    except Exception as e:
        # Top-Level-Fehler (unerwartet): optisch anzeigen und zentral resetten
        print("Fehler:", e)
        blinken_error()
        state = STATE_RESET
