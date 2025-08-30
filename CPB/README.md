# TA V – CPB Sensor Node + Cloud-LED-Steuerung (BLE-UART Bridge)

**Kurzfassung:** Circuit Playground Bluefruit (CPB) sendet Sensorwerte als CSV ueber **BLE UART** an einen Raspberry Pi (Central/Bridge) und nimmt **Textkommandos** fuer NeoPixel entgegen. Ausgelegt auf **bewertbare Kriterien**: Nachweisbarkeit (seq/Einheiten), Robustheit (Rate-Limit, Watchdog optional), Testbarkeit (SELFTEST, PING), Dokumentation (INFO/Heartbeat).

## Features
- 3 Sensoren: Licht (raw), Temperatur (C), Beschleunigung (m/s^2)
- CSV-Frames mit **Sequenznummer**, **Zeit in ms**, **Einheiten**, optional **Batteriespannung**
- Steuerkommandos: `FILL`, `PIX`, `BRI`, `RATE`, `PING`, `SELFTEST?`, `INFO?`
- **Heartbeat** alle 30 s, **Rate-Limit** fuer Kommandos (20/s), **optional Watchdog**

## Hardware / Software
- Board: Adafruit Circuit Playground Bluefruit (nRF52840)
- Firmware: CircuitPython (empfohlen: aktuelle 8.x/9.x)
- Lib-Bundle (in `CIRCUITPY/lib/`):
  - `adafruit_ble`, `adafruit_lis3dh`, `adafruit_thermistor`, `neopixel`

## Übersicht verwendeter Sensoren (CPB)

| Sensor             | Code-Stelle                                      | Messwert                     | Einheit       |
|--------------------|--------------------------------------------------|-------------------------------|---------------|
| **Lichtsensor**    | `light = analogio.AnalogIn(board.LIGHT)`         | `light.value` (Rohwert)       | 0 … 65535     |
| **Temperatur**     | `therm = adafruit_thermistor.Thermistor(...)`    | `therm.temperature`           | °C            |
| **Beschleunigung** | `lis = adafruit_lis3dh.LIS3DH_I2C(i2c)`          | `lis.acceleration` → ax,ay,az | m/s²          |
| **Batteriespannung (optional)** | `vbat = analogio.AnalogIn(board.VOLTAGE_MONITOR)` | `read_battery_mV()`           | mV (oder -1)  |


## Deployment auf das Board
1. CircuitPython auf das CPB flashen.
2. Adafruit CircuitPython **Library Bundle** entpacken, benoetigte Ordner nach `CIRCUITPY/lib/` kopieren.
3. Diese Datei als `code.py` auf `CIRCUITPY/` kopieren.
4. Nach Reset erscheint das Geraet per BLE als **`CPB_TA_V`**.

## Protokoll

**Infozeile (bei Connect oder `INFO?`):**
```
INFO,CPB,TA-V,<FW_VERSION>,<BUILD_DATE>,NODE=<id>,PIX=10,CMDS=FILL|PIX|BRI|RATE|INFO?|SELFTEST?|PING
```

**Heartbeat (alle 30 s):**
```
HB,ms=<now_ms>,node=<id>,ver=<FW_VERSION>
```

**Sensorframe (Standard 1 Hz, per `RATE` aenderbar):**
```
SENS,seq=<n>,ms=<t>,light_raw=<int>,light_f=<float>,temp_C=<float>,ax_ms2=<f>,ay_ms2=<f>,az_ms2=<f>,ax_f=<f>,ay_f=<f>,az_f=<f>,batt_mV=<int>
```
- `seq`: Sequenznummer ab 1
- `ms`: Monotonic-Time in Millisekunden (vom CPB)
- `*_f`: geglaettete Werte (EMA, alpha=0.2)
- `batt_mV`: -1 wenn nicht verfuegbar

**Kommandos (CSV, endet mit `\n`):**
- `FILL,<r>,<g>,<b>` – alle Pixel setzen (0..255)
- `PIX,<i>,<r>,<g>,<b>` – Pixel `i` setzen (0..9)
- `BRI,<0-100>` – Helligkeit in Prozent
- `RATE,<Hz>` – 0.2 .. 5.0 Hz Messrate
- `SELFTEST?` – prueft Sensor-Read und 1 Pixel, Rueckgabe: `SELFTEST,OK/ERR,...`
- `PING` – Rueckgabe: `OK,PING,ms=<now_ms>`
- `INFO?` – Infozeile erneut senden

**Antworten:**
- Erfolg: `OK,<TAG>,ms=<now_ms>[,extra]`
- Fehler: `ERR,<TAG>,ms=<now_ms>[,reason]`

## Bezug zu Beurteilungskriterien

- **Zielerreichung**: 2+ Sensoren, 1 Hz, CSV-Protokoll, LED-Steuerung – per Screencast/Log nachweisbar.
- **Nachvollziehbarkeit**: `seq`, `ms`, Einheiten, `INFO` mit `NODE_ID`, `FW_VERSION`, `BUILD_DATE`.
- **Robustheit**: optionaler Watchdog, Reconnect-Loop, Rate-Limit, Fehlercodes.
- **Testbarkeit**: `SELFTEST?`, `PING`, standardisierte `OK/ERR`; Heartbeat fuer Dauerlauf.
- **Sicherheit/Ordnung**: Board-Name eindeutig, Eingangsvalidierung (clamp), Index-Check, BRI-Clamp.

## Beispiel-Logs

```
INFO,CPB,TA-V,1.1.0,2025-08-30,NODE=CPB-01,PIX=10,CMDS=FILL|PIX|BRI|RATE|INFO?|SELFTEST?|PING
OK,BRI,ms=123456,Brightness=
OK,RATE,ms=124001,Hz=2.00
SENS,seq=1,ms=124500,light_raw=23456,light_f=23456.0,temp_C=23.12,ax_ms2=-0.100,ay_ms2=0.010,az_ms2=9.810,ax_f=-0.100,ay_f=0.010,az_f=9.810,batt_mV=4100
OK,FILL,ms=125000
HB,ms=154500,node=CPB-01,ver=1.1.0
```

## Pi-Bridge (Hinweis)
- Central verbindet auf Geraetename `CPB_TA_V`, liest UART-Zeilen und publiziert in Cloud-Feeds.
- Rueckkanal: Cloud-Feed -> Pi -> BLE UART (z. B. `FILL,0,10,0\n`).
- `PING` eignet sich zur Latenzmessung Cloud->CPB.

## Troubleshooting
- Keine Verbindung: Pruefen, ob andere Central bereits verbunden ist. Reset des CPB.
- Keine Libraries gefunden: Adafruit Bundle-Version passend zur CircuitPython-Version verwenden.
- Batteriespannung -1: Board hat keinen `VOLTAGE_MONITOR` oder Pin nicht verfuegbar.