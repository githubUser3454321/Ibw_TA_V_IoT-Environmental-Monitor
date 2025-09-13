# Title: TA V – CPB Sensor Node + Cloud-LED-Steuerung (BLE-UART Bridge)
# Author: Fabio Panteghini
# Date: 2025-09-13
# -----------------------------------------------------------------
#
# Zweck:
#  - Erfassung von mindestens 2 Umweltsensoren auf dem CPB:
#      * Temperatur (NTC-Thermistor, A9)
#      * Licht (ALS-PT19, A8)
#      * Bewegung (LIS3DH Beschleunigungssensor, I2C)
#  - Übertragung der Messwerte periodisch als Zeitreihe via BLE-UART an den Raspberry Pi
#  - Raspberry Pi dient als Bridge zur IoT-Cloud (z. B. Adafruit IO)
#  - Steuerung der NeoPixel (Farbe, Helligkeit, Reset etc.) erfolgt über Textkommandos
#    vom Raspberry Pi / Cloud an das CPB
#
# Datenfluss:
#  CPB (BLE UART Peripheral) <-> Raspberry Pi (BLE Central/Bridge) <-> IoT-Cloud
#
# Sendeformat (1 Hz):
#  SENS,seq=<n>,ms=<t>,temp_C=<float>,light_raw=<int>,light_norm=<float>,
#       ax_ms2=<f>,ay_ms2=<f>,az_ms2=<f>
#
# Empfangskommandos (UART-Text, CSV-basiert):
#  - FILL r g b       → setzt alle NeoPixel auf eine RGB-Farbe (0–255)
#  - FILLHEX RRGGBB   → setzt alle NeoPixel auf eine Hex-Farbe
#  - BRIGHT <0–100>   → setzt Helligkeit in %
#  - OFF              → schaltet alle NeoPixel aus
#  - RESET            → Reset der NeoPixel auf Standard
#  - GET / GET?       → gibt aktuellen Status (Farben, Helligkeit) zurück
#  - TEMP? / GETTEMP? → einmalige Temperaturmessung senden
#  - LIGHT?           → einmalige Lichtmessung senden
#  - SENS? / GETSENS? → eine komplette Sensordatenzeile senden
#  - TELEM <sek>      → setzt Periodendauer der Telemetrie (0 = aus)
#
# Abhängigkeiten (CircuitPython Bundle):
#  - adafruit_ble
#  - adafruit_lis3dh
#  - adafruit_thermistor
#  - neopixel
#
# Board: Adafruit Circuit Playground Bluefruit (nRF52840, LIS3DH integriert)
# Sprache: CircuitPython



import time
import board
import neopixel
import analogio
from adafruit_thermistor import Thermistor

from adafruit_ble import BLERadio
from adafruit_ble.advertising.standard import ProvideServicesAdvertisement
from adafruit_ble.services.nordic import UARTService

# Bewegungssensor: I2C + LIS3DH
import busio
from adafruit_lis3dh import LIS3DH_I2C, RANGE_2_G

# === NeoPixel Setup ===
NUM_PIXELS = 10
pixels = neopixel.NeoPixel(board.NEOPIXEL, NUM_PIXELS, brightness=0.06, auto_write=False)

# === Sensoren ===
# Temperatur: NTC am A9 (Alias TEMPERATURE, falls vorhanden)
THERM_PIN = getattr(board, "TEMPERATURE", board.A9)
thermistor = Thermistor(
    pin=THERM_PIN,
    series_resistor=10000.0,
    nominal_resistance=10000.0,
    nominal_temperature=25.0,
    b_coefficient=3380.0
)

# Licht: ALS-PT19 an A8, liefert 0..65535 (heller = groesser)
light = analogio.AnalogIn(board.A8)

# Bewegung: LIS3DH am internen I2C (nicht die Aussenpads!)
try:
    ACC_SCL = getattr(board, "ACCELEROMETER_SCL", board.SCL)
    ACC_SDA = getattr(board, "ACCELEROMETER_SDA", board.SDA)
    i2c = busio.I2C(ACC_SCL, ACC_SDA)
except Exception as e:
    print("I2C Init Fehler:", e)
    i2c = None

lis3dh = None
if i2c:
    for addr in (0x19, 0x18):
        try:
            lis3dh = LIS3DH_I2C(i2c, address=addr)
            lis3dh.range = RANGE_2_G
            print("LIS3DH gefunden @ " + hex(addr))
            break
        except Exception as e:
            print("LIS3DH nicht @ " + hex(addr) + " : " + str(e))

if lis3dh is None:
    print("WARN: Kein LIS3DH gefunden – Bewegung wird 0.0 gemeldet")

# Tiefpass fuer Beschleunigung
ax_f = 0.0
ay_f = 0.0
az_f = 0.0
alpha = 0.2  # 0..1; kleiner = staerker glaetten

# === BLE Setup ===
ble = BLERadio()
uart = UARTService()
adv = ProvideServicesAdvertisement(uart)
adv.complete_name = "CPB_TA_V"
ble.start_advertising(adv)

# === State Machine ===
STATE_WAIT, STATE_HANDLE, STATE_RESET, STATE_ERROR = range(4)
state = STATE_WAIT

farben = [(40, 40, 40)] * NUM_PIXELS
wait_t = time.monotonic()
wait_on = False
rx_buf = ""  # Zeilenpuffer fuer UART

# Telemetrie (Sekunden)
telemetry_period = 1.0
telemetry_t = time.monotonic()

# === Helper ===
def blinken_error(n=2, farbe=(50, 0, 0), dauer=0.15):
    """Blinkt n-mal in der angegebenen Farbe."""
    for _ in range(n):
        pixels.fill(farbe)
        pixels.show()
        time.sleep(dauer)
        pixels.fill((0, 0, 0))
        pixels.show()
        time.sleep(dauer)

def blink_wait():
    """Blaues Blinken solange keine Verbindung besteht."""
    global wait_t, wait_on
    if time.monotonic() - wait_t > 0.5:
        wait_t = time.monotonic()
        wait_on = not wait_on
        if wait_on:
            pixels.fill((0, 0, 50))
        else:
            pixels.fill((0, 0, 0))
        pixels.show()

def aktualisiere():
    """Alle LEDs entsprechend den gespeicherten Farben setzen."""
    for i in range(NUM_PIXELS):
        pixels[i] = farben[i]
    pixels.show()

def clamp8(x):
    return max(0, min(255, int(x)))

def set_all(r, g, b):
    """Alle LEDs auf eine RGB-Farbe setzen."""
    rgb = (clamp8(r), clamp8(g), clamp8(b))
    for i in range(NUM_PIXELS):
        farben[i] = rgb
    aktualisiere()

def set_all_hex(hexstr):
    """Alle LEDs auf Hexfarbe RRGGBB setzen."""
    hs = hexstr.strip().lstrip("#")
    if len(hs) != 6:
        return False
    r = int(hs[0:2], 16)
    g = int(hs[2:4], 16)
    b = int(hs[4:6], 16)
    set_all(r, g, b)
    return True

def reset_all():
    """Standardzustand fuer Start oder Reset."""
    for i in range(NUM_PIXELS):
        farben[i] = (40, 40, 40)
    pixels.brightness = 0.06
    pixels.fill((0, 0, 80))
    pixels.show()
    time.sleep(0.2)
    pixels.fill((0, 0, 0))
    pixels.show()
    aktualisiere()

def ok(msg):
    try:
        uart.write(("OK " + msg + "\n").encode("utf-8"))
    except Exception as e:
        print("UART write err:", e)

def err(msg):
    try:
        uart.write(("ERR " + msg + "\n").encode("utf-8"))
    except Exception as e:
        print("UART write err:", e)

def send_status():
    try:
        parts = []
        for i, (r, g, b) in enumerate(farben):
            parts.append("{}:{},{},{}".format(i, r, g, b))
        cols = ";".join(parts)
        uart.write(("STAT bright={} colors={}\n".format(int(pixels.brightness*100), cols)).encode("utf-8"))
    except Exception as e:
        print("UART write err:", e)

def ms():
    """Millisekunden seit Start."""
    return time.monotonic_ns() // 1_000_000

# === Sensors: Readouts ===
def read_temp_c():
    """Temperatur in °C lesen."""
    try:
        return float(thermistor.temperature)
    except Exception as e:
        print("ERR temp:", e)
        return float("nan")

def read_light():
    """Lichtwert lesen: raw 0..65535 sowie normiert 0..1.0."""
    try:
        raw = int(light.value)
        norm = raw / 65535.0
        return raw, norm
    except Exception as e:
        print("ERR light:", e)
        return -1, 0.0

def read_acc_ms2():
    """Beschleunigung in m/s^2 lesen (0,0,0 wenn Sensor fehlt)."""
    if lis3dh is None:
        return 0.0, 0.0, 0.0
    try:
        ax, ay, az = lis3dh.acceleration  # m/s^2
        return float(ax), float(ay), float(az)
    except Exception as e:
        print("ERR acc:", e)
        return 0.0, 0.0, 0.0

# === Telemetrie-Zeile senden ===
def send_sens_line():
    """SENS-Zeile mit Zeit, Temperatur, Licht und Bewegung senden."""
    global ax_f, ay_f, az_f
    t_c = read_temp_c()
    l_raw, l_norm = read_light()
    ax, ay, az = read_acc_ms2()

    # Tiefpass anwenden
    ax_f = (1 - alpha) * ax_f + alpha * ax
    ay_f = (1 - alpha) * ay_f + alpha * ay
    az_f = (1 - alpha) * az_f + alpha * az

    line = "SENS"
    line += ",ms={}".format(ms())
    line += ",temp_C={:.2f}".format(t_c)
    line += ",light_raw={},light_norm={:.4f}".format(l_raw, l_norm)
    line += ",ax_ms2={:.3f},ay_ms2={:.3f},az_ms2={:.3f}".format(ax, ay, az)
    line += ",ax_f={:.3f},ay_f={:.3f},az_f={:.3f}\n".format(ax_f, ay_f, az_f)
    line += "\n"
    try:
        uart.write(line.encode("utf-8"))
    except Exception as e:
        print("UART write err:", e)

# === Kommando-Parser ===
def handle_command(line: str):
    """Parser fuer UART-Kommandos vom Pi/Cloud."""
    global telemetry_period
    if not line:
        return
    parts = line.strip().split()
    if not parts:
        return
    cmd = parts[0].upper()

    try:
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

        elif cmd == "RESET":
            reset_all()
            ok("RESET")

        elif cmd in ("GET?", "GET"):
            send_status()

        elif cmd in ("GETTEMP?", "TEMP?"):
            t_c = read_temp_c()
            try:
                uart.write(("TEMP C={:.2f}\n".format(t_c)).encode("utf-8"))
            except Exception as e:
                print("UART write err:", e)

        elif cmd in ("GETLIGHT?", "LIGHT?"):
            l_raw, l_norm = read_light()
            try:
                uart.write(("LIGHT raw={} norm={:.4f}\n".format(l_raw, l_norm)).encode("utf-8"))
            except Exception as e:
                print("UART write err:", e)

        elif cmd in ("GETACC?", "ACC?"):
            ax, ay, az = read_acc_ms2()
            try:
                uart.write(("ACC ax={:.3f} ay={:.3f} az={:.3f}\n".format(ax, ay, az)).encode("utf-8"))
            except Exception as e:
                print("UART write err:", e)

        elif cmd in ("GETSENS?", "SENS?"):
            send_sens_line()

        elif cmd == "TELEM" and len(parts) == 2:
            val_raw = parts[1].upper()
            if val_raw == "OFF":
                telemetry_period = 0.0
                ok("TELEM OFF")
            else:
                val = float(parts[1])
                if val < 0.0:
                    val = 0.0
                telemetry_period = val
                ok("TELEM {}".format(val))

        else:
            err("unknown")

    except Exception as e:
        print("Cmd error:", e)
        err("format")

# === Main Loop ===
while True:
    try:
        if state == STATE_WAIT:
            blink_wait()
            if ble.connected:
                pixels.fill((0, 50, 0))
                pixels.show()
                time.sleep(0.2)
                pixels.fill((0, 0, 0))
                pixels.show()
                reset_all()
                rx_buf = ""
                telemetry_t = time.monotonic()
                state = STATE_HANDLE

        elif state == STATE_HANDLE:
            # UART-Kommandos lesen (Zeilen)
            if uart.in_waiting:
                raw = uart.read(uart.in_waiting)
                if raw:
                    rx_buf += raw.decode("utf-8", errors="ignore")
                    while "\n" in rx_buf:
                        line, rx_buf = rx_buf.split("\n", 1)
                        handle_command(line.strip())

            # Telemetrie senden
            if telemetry_period > 0.0:
                now = time.monotonic()
                if now - telemetry_t >= telemetry_period:
                    telemetry_t = now
                    send_sens_line()

            # Disconnect-Handling
            if not ble.connected:
                ble.start_advertising(adv)
                state = STATE_WAIT

        elif state == STATE_RESET:
            reset_all()
            state = STATE_HANDLE

        elif state == STATE_ERROR:
            blinken_error()
            state = STATE_HANDLE

    except Exception as e:
        print("Fehler:", e)
        blinken_error()
        state = STATE_HANDLE
