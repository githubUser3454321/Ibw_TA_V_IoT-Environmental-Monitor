# Raspberry Pi BLE Bridge für CPB

Dieses Projekt verbindet sich per Bluetooth Low Energy (BLE) mit einem Adafruit Circuit Playground Bluefruit (CPB), 
empfängt Sensordaten und erlaubt das Senden von Befehlen wie `PING`, `INFO?` oder LED-Steuerung.  

---

## Voraussetzungen (einmalig)

Auf deinem Raspberry Pi folgende Pakete installieren:

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv bluetooth bluez
```

Prüfen, ob Bluetooth läuft:

```bash
sudo systemctl status bluetooth
```

Falls gestoppt: `sudo systemctl start bluetooth`

---

## Virtuelle Umgebung (.venv) erstellen und aktivieren

Projektverzeichnis wechseln:

```bash
cd <path to your Raspberry_Pi project>
```

Virtuelle Umgebung anlegen:

```bash
python3 -m venv .venv
```

Aktivieren (Linux/Raspberry Pi):

```bash
source .venv/bin/activate
```

---

## Abhängigkeiten installieren

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Script starten

```bash
python3 cpb_ble_bridge.py
```

---

## Nutzung

- Nach erfolgreicher Verbindung mit dem CPB erscheinen automatisch alle empfangenen Datenzeilen in der Konsole.  
- Eigene Kommandos kannst du eintippen (z. B. `PING`, `INFO?`, `FILL,255,0,0`) → sie werden an den CPB gesendet.  
- Beenden mit `Ctrl + C`.  

---

## Tipps

- Stelle sicher, dass dein CPB im **Advertise-Mode** ist (`advertisement.complete_name = "CPB_TA_V"`).  
- Wenn mehrere CPBs vorhanden sind, kannst du im Python-Script den **Gerätenamen oder die MAC-Adresse** anpassen.  
- Bei Problemen mit Berechtigungen:  
  ```bash
  sudo setcap 'cap_net_raw,cap_net_admin+eip' $(readlink -f $(which python3))
  ```
  → damit kann Python BLE nutzen, ohne dass du das Script mit `sudo` starten musst.  
