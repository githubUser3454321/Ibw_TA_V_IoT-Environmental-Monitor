# README – TA V CPB Sensor Node + Cloud-LED-Steuerung

## 1 Einleitung
Dieses Dokument beschreibt den Aufbau und die Nutzung des Codes für das IoT-Projekt im Rahmen der Transferaufgabe V.  
Der Code läuft auf dem **Adafruit Circuit Playground Bluefruit (CPB)** und kommuniziert per Bluetooth Low Energy (BLE) mit einem **Raspberry Pi**.  
Der Raspberry Pi leitet die Sensordaten an eine IoT-Cloud (z. B. Adafruit IO) weiter und überträgt Steuerbefehle aus der Cloud an den CPB.

---

## 2 Ziel und Zweck
- Erfassen von mindestens **zwei Umweltsensoren** (Licht, Temperatur, Bewegung) auf dem CPB.  
- Übertragung dieser Daten über BLE → Raspberry Pi → WLAN/Ethernet → IoT-Cloud.  
- **Darstellung der Sensordaten als Zeitreihe** in der Cloud (z. B. Dashboard/Charts).  
- **Fernsteuerung der NeoPixel-LEDs** aus der Cloud (Farbe, Helligkeit).

---

## 3 Datenfluss
```
CPB (BLE UART Peripheral)
    <-> Raspberry Pi (BLE Central / IoT-Bridge)
        <-> IoT-Cloud (Adafruit IO)
```

---

## 4 Funktionsumfang

### 4.1 Sendedaten (Sensordaten vom CPB)
Format (CSV, 1 Hz):
```
SENS,<ms>,<light_raw>,<temp_C>,<ax>,<ay>,<az>
```
Beispiel:
```
SENS,12345,240,22.37,0.001,-0.054,9.812
```

### 4.2 Empfangskommandos (vom Pi / Cloud an CPB)
- `FILL,<r>,<g>,<b>` – Setzt alle NeoPixel auf eine Farbe  
- `PIX,<index>,<r>,<g>,<b>` – Setzt ein einzelnes NeoPixel  
- `BRI,<0-100>` – Helligkeit aller NeoPixel einstellen  
- `INFO?` – Liefert eine Infozeile mit Boarddaten zurück

---

## 5 Installation

### 5.1 Circuit Playground Bluefruit (CPB)
1. CircuitPython installieren: [circuitpython.org](https://circuitpython.org/board/circuitplayground_bluefruit/)  
2. Im Ordner `/lib` folgende Libraries ablegen:
   - `adafruit_ble`
   - `adafruit_lis3dh.mpy`
   - `adafruit_thermistor.mpy`
   - `neopixel.mpy`
3. Datei `code.py` auf den CPB kopieren.  

### 5.2 Raspberry Pi
1. Python 3 installieren.  
2. Dependencies via pip installieren:
```bash
pip install -r requirements_full.txt
```
3. Enthaltene Pakete:
   - `bleak` (BLE Kommunikation)  
   - `adafruit-io` (Cloud-Anbindung)  
   - `adafruit-circuitpython-ble`  
   - `adafruit-circuitpython-lis3dh`  
   - `adafruit-circuitpython-thermistor`  
   - `adafruit-circuitpython-neopixel`  

---

## 6 Nutzung
1. Raspberry Pi startet das Bridge-Skript (stellt BLE-Verbindung her und verbindet zur Cloud).  
2. CPB sendet Sensordaten automatisch alle 1 Sekunde.  
3. Raspberry Pi publiziert Daten in der IoT-Cloud.  
4. Cloud-Befehle werden vom Pi empfangen und via BLE an das CPB weitergereicht, wodurch die NeoPixel gesteuert werden.

---

## 7 Hinweise
- Eindeutiger BLE-Name des Boards: **CPB_TA_V**  
- Standard-Sensorrate: 1 Hz (konfigurierbar in `SENS_INTERVAL`)  
- Erweiterungen: Weitere Sensoren oder zusätzliche Befehle können einfach ergänzt werden.

---

## 8 Autor
Fabio Panteghini  
August 2025  
