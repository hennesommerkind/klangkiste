# Klangkiste

Eine komfortable RFID-Musikbox für den Raspberry Pi: Karte auflegen, Hörspiel läuft.
Alles Weitere erledigt eine Weboberfläche – Musik hochladen, Karten zuweisen, Knöpfe
einrichten, sichern.

- **Weboberfläche** fürs Handy und den Computer: Musikverwaltung mit Upload, Cover, Karten
- **Knöpfe ohne Aufschrauben**: Anlernmodus erkennt, an welchem GPIO ein Knopf hängt
- **MQTT** mit automatischer Erkennung in Home Assistant
- **Im Haus hören**: MP3-Stream für MusicCast (über Home Assistant), Spotify Connect
- **Sicherung** zu IONOS HiDrive auf Knopfdruck oder nach Zeitplan, mit Wiederherstellung
- **Hörspiele und Musik getrennt**, Serien mit Folgen als Unterordner
- **Kartenmodus** „Auflegen und liegen lassen“ (wegnehmen = Pause) oder „Antippen“
- Hörspiele werden an der letzten Stelle fortgesetzt, Musik beginnt von vorn

## Hardware

Gebaut für: Raspberry Pi 3 B+, RC522-Kartenleser (SPI), PCM5102-Soundkarte im
HiFiBerry-DAC-Stil, drei Knöpfe (Zurück GPIO 27, Play/Pause GPIO 12, Vor GPIO 5).

Kartenleser: SDA → GPIO 8, SCK → GPIO 11, MOSI → GPIO 10, MISO → GPIO 9, RST → GPIO 25.

## Installation

1. Mit dem **Raspberry Pi Imager** „Raspberry Pi OS Lite (64-bit)“ auf eine SD-Karte schreiben.
   In den Einstellungen des Imagers WLAN, einen Benutzer mit eigenem Passwort, den
   Hostnamen (z. B. `klangkiste`) und SSH einrichten.
2. Karte einlegen, Box starten, per SSH anmelden und ausführen:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/hennesommerkind/klangkiste/main/install.sh | sudo bash
   sudo reboot
   ```

3. Im Browser `http://klangkiste.local` öffnen.

Andere Soundkarte? `SOUND_OVERLAY=… SOUND_CARD=…` vor den Befehl setzen (siehe `install.sh`).

## Musik ordnen

```
Hörspiele/
  Der Grüffelo/              ← einzelnes Hörspiel: Titel direkt im Ordner
  Bibi Blocksberg/           ← Serie
    Folge 2 – Der Hexenbesen/
    Folge 10 – Kartoffelbrei/
Musik/
  Rolf Zuckowski/
```

Eine Karte kann auf eine einzelne Folge oder auf die ganze Serie zeigen (dann laufen alle
Folgen nacheinander, in natürlicher Reihenfolge: Folge 2 vor Folge 10).

## MQTT

Status (JSON, retained): `klangkiste/state` · Erreichbarkeit: `klangkiste/status` ·
gelesene Karten: `klangkiste/card`

Befehle an `klangkiste/cmd/…`: `play_pause`, `play`, `pause`, `next`, `prev`, `stop`,
`vol_up`, `vol_down`, `volume` (0–100), `stream` (ON/OFF), `play_folder` (Ordnername),
`play_url` (Adresse), `shutdown`.

## Technik

- `klangkiste/` – Python-Dienst (aiohttp), steuert MPD, Leser, Knöpfe, MQTT, Sicherung
- `web/` – Oberfläche ohne Build-Schritt (HTML, CSS, JavaScript)
- `system/` – Vorlagen für MPD, ALSA, systemd
- Daten liegen in `/var/lib/klangkiste` (Musik, `config.json`, `cards.json`)
- Protokoll ansehen: `journalctl -u klangkiste -e`
- Test ohne Hardware: `python3 tests/fake_mpd.py /tmp/kk/music 6611 &` und
  `python3 -m klangkiste --mock --data /tmp/kk --port 8099 --mpd-port 6611`

Die Sicherungsdatei enthält die Einstellungen einschließlich der gespeicherten
Passwörter für MQTT und HiDrive – bitte entsprechend aufbewahren.
