# TA V – CPB Sensor Node + Cloud-LED-Steuerung (BLE-UART Bridge)

**Kurzfassung:** Circuit Playground Bluefruit (CPB) sendet **Temperatur- und Lichtwerte** als CSV ueber **BLE UART** an einen Raspberry Pi (Central/Bridge) und nimmt **Textkommandos** fuer NeoPixel entgegen.  

## Features
- 2 Sensoren: Licht (raw + normiert), Temperatur (°C)  
- CSV-Frames mit Zeitstempel (ms) und Einheiten  
- Steuerkommandos: `FILL`, `FILLHEX`, `BRIGHT`, `OFF`, `RESET`, `GET`, `TEMP?`, `LIGHT?`, `SENS?`, `TELEM`  
- Debug-Ausgaben via Serial Monitor (`DBG >>`, `DBG <<`)  

## Hardware / Software
- Board: Adafruit Circuit Playground Bluefruit (nRF52840)  
- Firmware: CircuitPython (empfohlen: aktuelle 8.x/9.x)  
- Lib-Bundle (in `CIRCUITPY/lib/`):  
  - `adafruit_ble`, `adafruit_thermistor`, `neopixel`  

## Übersicht verwendeter Sensoren (CPB)

| Sensor          | Code-Stelle                                   | Messwert              | Einheit        |
|-----------------|-----------------------------------------------|-----------------------|----------------|
| **Lichtsensor** | `light = analogio.AnalogIn(board.A8)`         | `light.value` (raw)   | 0 … 65535      |
|                 |                                               | `raw/65535.0` (norm.) | 0.0 … 1.0      |
| **Temperatur**  | `thermistor = Thermistor(board.A9, …)`        | `thermistor.temperature` | °C          |

## Deployment auf das Board
1. CircuitPython auf das CPB flashen.  
2. Adafruit CircuitPython **Library Bundle** entpacken, benoetigte Ordner nach `CIRCUITPY/lib/` kopieren.  
3. Diese Datei als `code.py` auf `CIRCUITPY/` kopieren.  
4. Nach Reset erscheint das Geraet per BLE als **`CPB_TA_V`**.  

## Protokoll

**Sensorframe (Standard 1 Hz, per `TELEM` aenderbar):**
```
SENS,ms=<t>,temp_C=<float>,light_raw=<int>,light_norm=<float>
```
- `ms`: Monotonic-Time in Millisekunden (vom CPB)  
- `temp_C`: Temperatur in °C  
- `light_raw`: Rohwert des Lichtsensors (0–65535)  
- `light_norm`: normiert auf 0.0000–1.0000  

**Kommandos (Text, endet mit `\n`):**
- `FILL r g b` – alle Pixel setzen (0..255)  
- `FILLHEX RRGGBB` – Hexfarbe setzen  
- `BRIGHT <0–100>` – Helligkeit in Prozent  
- `OFF` – alle Pixel ausschalten  
- `RESET` – NeoPixel auf Standard  
- `GET` / `GET?` – Status (Farben, Helligkeit)  
- `TEMP?` / `GETTEMP?` – einmalige Temperaturmessung  
- `LIGHT?` – einmalige Lichtmessung  
- `SENS?` / `GETSENS?` – eine Sensordatenzeile senden  
- `TELEM <sek>` – Intervall fuer Telemetrie setzen (0 = aus)  

**Antworten:**
- Erfolg: `OK <TAG>`  
- Fehler: `ERR <TAG>`  

## Bezug zu Beurteilungskriterien
- **Zielerreichung:** 2 Sensoren, CSV-Protokoll, LED-Steuerung.  
- **Nachvollziehbarkeit:** Zeitstempel in ms, Einheiten im Payload.  
- **Robustheit:** Reconnect-Loop, Fehlerausgaben via `ERR`.  
- **Testbarkeit:** Einzelabruf via `TEMP?`, `LIGHT?`, `SENS?`.  
- **Dokumentation:** Klar definierte Protokolle, Debug-Ausgaben im Serial Monitor.  

## Beispiel-Logs (Serial Monitor)

```
DBG: Advertising gestartet als CPB_TA_V
DBG: BLE verbunden
DBG >> SENS,ms=123456,temp_C=23.47,light_raw=23456,light_norm=0.3580
DBG << FILL 0 20 0
DBG >> OK FILL 0 20 0
DBG << TEMP?
DBG >> TEMP C=23.47
DBG: BLE getrennt – starte Advertising
```
