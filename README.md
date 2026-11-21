# Klangkiste

Eine komfortable RFID-Musikbox für den Raspberry Pi: Karte auflegen, Hörspiel läuft.
Alles Weitere erledigt eine Weboberfläche – Musik hochladen, Karten zuweisen, Knöpfe
einrichten, sichern.

- **Weboberfläche** fürs Handy und den Computer: Musikverwaltung mit Upload, Cover, Karten
- **Knöpfe ohne Aufschrauben**: Anlernmodus erkennt, an welchem GPIO ein Knopf hängt
- **MQTT** mit automatischer Erkennung in Home Assistant
- **Im Haus hören**: MP3-Stream für MusicCast (über Home Assistant)
- **Spotify Connect** (go-librespot): Spotify pausiert die Figur und gibt danach an sie zurück,
  die Knöpfe steuern immer die Quelle, die zuletzt gespielt hat
- **Bluetooth-Kopfhörer** (bluez-alsa): koppeln in der Oberfläche, automatisch verbinden,
  Ton nur über Kopfhörer, Pause beim Trennen, eigene Höchstlautstärke
- **Not-WLAN**: ohne bekanntes Netz öffnet die Box nach einer Minute ein eigenes WLAN
  („Klangkiste“, Kennwort einstellbar), Oberfläche dann unter http://10.42.0.1
- **Ansagen**: Startton beim Systemstart; die Box sagt ihre IP-Adresse, wenn Play/Pause
  drei Sekunden gehalten wird und nachdem man ihr über das Not-WLAN ein Netz beigebracht hat
- **Lämpchen aus**: rote und grüne LED des Raspberry Pi in den Einstellungen abschaltbar
- **Suche** in Hörspielen, Musik (Ordner und Titel) und Karten
- **Web-App**: auf dem Startbildschirm ablegen (iPhone vollständig; Android mit voller
  App-Funktion nur über HTTPS bzw. wenn Chrome die Adresse als sicher einstuft)
- **Update-Knopf** in der Oberfläche: holt die neue Version und spielt auch Systemänderungen ein
- **Sicherung** zu IONOS HiDrive auf Knopfdruck oder nach Zeitplan, mit Wiederherstellung
- **Hörspiele und Musik getrennt**, Serien mit Folgen als Unterordner
- **Kartenmodus** „Auflegen und liegen lassen“ (wegnehmen = Pause) oder „Antippen“
- Hörspiele werden an der letzten Stelle fortgesetzt, Musik beginnt von vorn

## Hardware

Gebaut für: Raspberry Pi 3 B+, RC522-Kartenleser (SPI), PCM5102-Soundkarte im
HiFiBerry-DAC-Stil, drei Knöpfe (Zurück GPIO 27, Play/Pause GPIO 12, Vor GPIO 5).

Kartenleser: SDA → GPIO 8, SCK → GPIO 11, MOSI → GPIO 10, MISO → GPIO 9, RST → GPIO 25.

## Drehregler (optional)

KY-040/HW-040 für die Lautstärke: SW → GPIO 22 (Pin 15), CLK → GPIO 23 (Pin 16), „+“ → 3,3 V (Pin 17,
nicht 5 V), DT → GPIO 24 (Pin 18), GND → Pin 20. Anschlüsse, Schrittweite, Drehrichtung und die
Funktion beim Drücken (Standard: Stumm / Laut) stehen unter Einstellungen › Knöpfe.

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

Viele Folgen auf einmal: Ist jede Folge eine einzige Datei, in der Serie mehrere Dateien
hochladen – „Jede Datei als eigene Folge“ (in einer leeren Serie als Schalter, in einer Serie
mit Folgen automatisch) legt pro Datei einen Unterordner mit ihrem Namen an.

## MQTT

Status (JSON, retained): `klangkiste/state` · Erreichbarkeit: `klangkiste/status` ·
gelesene Karten: `klangkiste/card`

Befehle an `klangkiste/cmd/…`: `play_pause`, `play`, `pause`, `next`, `prev`, `stop`,
`vol_up`, `vol_down`, `volume` (0–100), `stream` (ON/OFF), `play_folder` (Ordnername),
`play_url` (Adresse), `shutdown`.

## Android-App

Im Ordner `android/` liegt eine schlanke App (WebView-Hülle um die Weboberfläche). Sie findet die Box
per mDNS (`_klangkiste._tcp`, von Avahi angekündigt) oder über bekannte Namen und merkt sich die Adresse.
GitHub Actions baut sie bei jeder Änderung und legt sie als Release `klangkiste.apk` ab; die Box lädt sie
beim Update und bietet sie unter Einstellungen › „Als App installieren“ zum Herunterladen an.
Der Signaturschlüssel liegt nicht im Repository: Er kommt aus den Repository-Secrets `KLANGKISTE_KEYSTORE`
(Base64) und `KLANGKISTE_KEYSTORE_PASS`, sonst aus dem Cache der Actions. Verfällt der Cache, wird die App
mit neuem Schlüssel gebaut und muss einmal deinstalliert und neu installiert werden.

## Netzlaufwerk

Unter Einstellungen › Netzlaufwerk lässt sich der Musikordner im Heimnetz freigeben (Samba, nur mit
Kennwort, Benutzer `klangkiste`). Am Computer einbinden: Windows `\\\\<Adresse der Box>\\Klangkiste`,
Mac `smb://<Adresse der Box>/Klangkiste`. Neue Dateien erkennt die Box nach etwa einer halben Minute.
Ab Werk ist die Freigabe aus.

## Wenn die Box schlecht erreichbar ist

`sudo bash /opt/klangkiste/system/diagnose.sh` sammelt Dienste, WLAN-Signal, Stromsparmodus,
Unterspannung, Abbrüche und die Namensauflösung in einer Ausgabe (ohne Kennwörter).

## Technik

- `klangkiste/` – Python-Dienst (aiohttp), steuert MPD, Leser, Knöpfe, MQTT, Sicherung
- `web/` – Oberfläche ohne Build-Schritt (HTML, CSS, JavaScript)
- `system/` – Vorlagen für MPD, ALSA, systemd
- Daten liegen in `/var/lib/klangkiste` (Musik, `config.json`, `cards.json`)
- Protokoll ansehen: `journalctl -u klangkiste -e`, Spotify: `journalctl -u go-librespot -e`,
  Kopfhörer: `journalctl -u bluealsa -u bluetooth -e`
- Updates: Einstellungen › System › „Jetzt aktualisieren“ startet `klangkiste-update.service`
  (root), der `/opt/klangkiste` aus GitHub aktualisiert und `install.sh --update` ausführt.
  Das Programmverzeichnis gehört root, der Dienst `klangkiste` kann seinen Code nicht ändern.
- Test ohne Hardware: `python3 tests/fake_mpd.py /tmp/kk/music 6611 &`,
  `python3 tests/fake_librespot.py 3699 &`, für Kopfhörer `PATH=tests/fakebin:$PATH` und
  `python3 -m klangkiste --mock --data /tmp/kk --port 8099 --mpd-port 6611 --spotify-url http://127.0.0.1:3699`

Die Sicherungsdatei enthält die Einstellungen einschließlich der gespeicherten
Passwörter für MQTT und HiDrive – bitte entsprechend aufbewahren.
