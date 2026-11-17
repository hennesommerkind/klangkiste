#!/bin/bash
# Klangkiste – Installation auf einem frischen Raspberry Pi OS Lite (Bookworm oder neuer).
#
#   curl -fsSL https://raw.githubusercontent.com/hennesommerkind/klangkiste/main/install.sh | sudo bash
#
# Das Skript kann gefahrlos mehrfach ausgeführt werden; der Update-Knopf der
# Oberfläche ruft es mit --update auf. Einstellbar über Umgebungsvariablen:
#   SOUND_OVERLAY  Soundkarten-Overlay   (Standard: hifiberry-dac)
#   SOUND_CARD     ALSA-Kartenname       (Standard: sndrpihifiberry)
#   SPOTIFY=0      Spotify Connect nicht installieren
set -euo pipefail

main() {
REPO_URL="${REPO_URL:-https://github.com/hennesommerkind/klangkiste.git}"
APP=/opt/klangkiste
DATA=/var/lib/klangkiste
SOUND_OVERLAY="${SOUND_OVERLAY:-hifiberry-dac}"
SOUND_CARD="${SOUND_CARD:-sndrpihifiberry}"
SPOTIFY="${SPOTIFY:-1}"
UPDATE=0; [ "${1:-}" = "--update" ] && UPDATE=1

say() { printf '\n\033[1;33m▶ %s\033[0m\n' "$*"; }
[ "$(id -u)" = 0 ] || { echo "Bitte mit sudo ausführen."; exit 1; }
export DEBIAN_FRONTEND=noninteractive

PKGS="git curl ca-certificates mpd alsa-utils rclone python3 python3-aiohttp python3-paho-mqtt python3-gpiozero python3-lgpio python3-spidev python3-mutagen"
missing=""
for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"; done
if [ -n "$missing" ] || [ "$UPDATE" = 0 ]; then
    say "Pakete installieren"
    apt-get update -q
    # shellcheck disable=SC2086
    apt-get install -y -q --no-install-recommends $PKGS
fi
# python-mpd2 heißt je nach Debian-Version unterschiedlich
if ! python3 -c "import mpd" 2>/dev/null; then
    apt-get install -y -q --no-install-recommends python3-mpd2 2>/dev/null \
        || apt-get install -y -q --no-install-recommends python3-mpd 2>/dev/null \
        || { apt-get install -y -q python3-pip && pip3 install --break-system-packages python-mpd2; }
fi

BT_PKGS="bluez bluez-alsa-utils libasound2-plugin-bluez"
bt_missing=""
for p in $BT_PKGS; do dpkg -s "$p" >/dev/null 2>&1 || bt_missing="$bt_missing $p"; done
if [ -n "$bt_missing" ]; then
    say "Bluetooth-Pakete installieren"
    [ -n "$missing" ] || [ "$UPDATE" = 0 ] || apt-get update -q
    # shellcheck disable=SC2086
    apt-get install -y -q --no-install-recommends $BT_PKGS \
        || echo "Bluetooth-Pakete ließen sich nicht installieren – die Box läuft auch ohne Kopfhörer."
fi

say "Benutzer und Ordner anlegen"
id klangkiste >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin klangkiste
for g in audio gpio spi bluetooth; do getent group "$g" >/dev/null && usermod -aG "$g" klangkiste; done
install -d -o klangkiste -g klangkiste -m 755 "$DATA" "$DATA/music" "$DATA/spotify"

say "Programm holen"
# Das Programm gehört root: Die Klangkiste selbst darf ihren Code nicht ändern,
# Updates spielt der Update-Dienst (root) ein.
git config --system --get-all safe.directory 2>/dev/null | grep -qx "$APP" \
    || git config --system --add safe.directory "$APP"
if [ -d "$APP/.git" ]; then
    chown -R root:root "$APP"
    if [ "$UPDATE" = 0 ]; then
        git -C "$APP" fetch --depth 50 origin main
        git -C "$APP" reset --hard origin/main
    fi
else
    rm -rf "$APP"
    git clone --depth 50 "$REPO_URL" "$APP"
fi

say "Soundkarte und Kartenleser einschalten"
CFG=/boot/firmware/config.txt; [ -f "$CFG" ] || CFG=/boot/config.txt
sed -i 's/^dtparam=audio=on/#dtparam=audio=on/' "$CFG"
grep -q '^dtparam=spi=on' "$CFG" || echo 'dtparam=spi=on' >> "$CFG"
grep -q "^dtoverlay=$SOUND_OVERLAY" "$CFG" || echo "dtoverlay=$SOUND_OVERLAY" >> "$CFG"
sed "s/CARDNAME/$SOUND_CARD/g" "$APP/system/asound.conf" > /etc/asound.conf

say "Abspieler (MPD) einrichten"
MPD_CHANGED=0
if ! cmp -s "$APP/system/mpd.conf" /etc/mpd.conf; then
    install -m 644 "$APP/system/mpd.conf" /etc/mpd.conf
    MPD_CHANGED=1
fi
systemctl enable mpd.service
# Erst danach maskieren: "enable mpd.service" schaltet den Socket sonst wieder ein,
# und über den Socket wäre MPD im ganzen Heimnetz erreichbar.
systemctl mask --now mpd.socket 2>/dev/null || true
if [ "$MPD_CHANGED" = 1 ] || ! systemctl is-active -q mpd.service; then systemctl restart mpd.service || true; fi

say "Protokolle begrenzen"
install -d /etc/systemd/journald.conf.d
JCONF=$(printf '[Journal]\nSystemMaxUse=50M\nRuntimeMaxUse=20M\n')
if [ "$(cat /etc/systemd/journald.conf.d/klangkiste.conf 2>/dev/null)" != "$JCONF" ]; then
    echo "$JCONF" > /etc/systemd/journald.conf.d/klangkiste.conf
    systemctl restart systemd-journald
fi

if [ "$SPOTIFY" = 1 ]; then
    say "Spotify Connect (go-librespot) einrichten"
    # Frühere Installationen nutzten raspotify – das hat keine Steuerschnittstelle für die Knöpfe.
    if dpkg -s raspotify >/dev/null 2>&1; then
        systemctl disable --now raspotify 2>/dev/null || true
        apt-get purge -y -q raspotify || true
    fi
    if [ ! -x /usr/local/bin/go-librespot ]; then
        case "$(uname -m)" in
            aarch64) VARIANT=arm64 ;; armv7l|armv6l) VARIANT=armv6_rpi ;; x86_64) VARIANT=x86_64 ;;
            *) VARIANT="" ;;
        esac
        if [ -n "$VARIANT" ]; then
            TMP=$(mktemp -d)
            if curl -fsSL -o "$TMP/glr.tar.gz" \
                "https://github.com/devgianlu/go-librespot/releases/latest/download/go-librespot_linux_${VARIANT}.tar.gz"; then
                tar -xzf "$TMP/glr.tar.gz" -C "$TMP" go-librespot
                install -m 755 "$TMP/go-librespot" /usr/local/bin/go-librespot
            else
                echo "go-librespot ließ sich nicht herunterladen – die Box läuft auch ohne Spotify."
            fi
            rm -rf "$TMP"
        fi
    fi
    if [ -x /usr/local/bin/go-librespot ]; then
        install -m 644 -o klangkiste -g klangkiste "$APP/system/go-librespot.yml" "$DATA/spotify/config.yml"
        install -m 644 "$APP/system/go-librespot.service" /etc/systemd/system/go-librespot.service
    fi
fi

say "Bluetooth für Kopfhörer einrichten"
if command -v bluetoothctl >/dev/null; then
    command -v rfkill >/dev/null && rfkill unblock bluetooth || true
    systemctl enable --now bluetooth.service 2>/dev/null || true
    BA=$(command -v bluealsad || command -v bluealsa || true)   # ab bluez-alsa 5.0 heißt das Programm bluealsad
    if [ -n "$BA" ] && systemctl cat bluealsa.service >/dev/null 2>&1; then
        install -d /etc/systemd/system/bluealsa.service.d
        printf '[Service]\nExecStart=\nExecStart=%s -S -p a2dp-source\n' "$BA" > /etc/systemd/system/bluealsa.service.d/klangkiste.conf
        systemctl daemon-reload
        systemctl enable bluealsa.service 2>/dev/null || true
        systemctl restart bluealsa.service || true
    else
        echo "bluez-alsa nicht gefunden – Kopfhörer werden nicht unterstützt."
    fi
fi

say "Dienste einrichten"
cat > /etc/sudoers.d/klangkiste <<'EOF'
klangkiste ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff, /usr/bin/systemctl restart klangkiste, /usr/bin/systemctl restart go-librespot, /usr/bin/systemctl start --no-block klangkiste-update.service
EOF
chmod 440 /etc/sudoers.d/klangkiste
install -m 644 "$APP/system/klangkiste.service" /etc/systemd/system/klangkiste.service
install -m 644 "$APP/system/klangkiste-update.service" /etc/systemd/system/klangkiste-update.service
systemctl daemon-reload
systemctl enable klangkiste.service
if [ -f /etc/systemd/system/go-librespot.service ]; then
    systemctl enable go-librespot.service
    systemctl restart go-librespot.service || true
fi
systemctl restart klangkiste.service

IP=$(hostname -I | awk '{print $1}')
if [ "$UPDATE" = 1 ]; then
    say "Update eingespielt"
else
    say "Fertig!"
    echo "Bitte jetzt einmal neu starten:   sudo reboot"
    echo "Danach im Browser öffnen:         http://$IP   oder   http://$(hostname).local"
fi
}

# Alles steht in main(): bash liest das Skript so vollständig ein, bevor ein
# Update die Datei während der Ausführung ersetzt.
main "$@"
exit
