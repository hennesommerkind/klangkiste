#!/bin/bash
# Klangkiste – Installation auf einem frischen Raspberry Pi OS Lite (Bookworm oder neuer).
#
#   curl -fsSL https://raw.githubusercontent.com/hennesommerkind/klangkiste/main/install.sh | sudo bash
#
# Das Skript kann gefahrlos mehrfach ausgeführt werden. Einstellbar über Umgebungsvariablen:
#   SOUND_OVERLAY  Soundkarten-Overlay   (Standard: hifiberry-dac)
#   SOUND_CARD     ALSA-Kartenname       (Standard: sndrpihifiberry)
#   SPOTIFY=0      Spotify Connect nicht installieren
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/hennesommerkind/klangkiste.git}"
APP=/opt/klangkiste
DATA=/var/lib/klangkiste
SOUND_OVERLAY="${SOUND_OVERLAY:-hifiberry-dac}"
SOUND_CARD="${SOUND_CARD:-sndrpihifiberry}"
SPOTIFY="${SPOTIFY:-1}"

say() { printf '\n\033[1;33m▶ %s\033[0m\n' "$*"; }
[ "$(id -u)" = 0 ] || { echo "Bitte mit sudo ausführen."; exit 1; }
export DEBIAN_FRONTEND=noninteractive

say "Pakete installieren"
apt-get update -q
apt-get install -y -q --no-install-recommends git curl ca-certificates mpd alsa-utils rclone \
    python3 python3-aiohttp python3-paho-mqtt python3-gpiozero python3-lgpio python3-spidev python3-mutagen
# python-mpd2 heißt je nach Debian-Version unterschiedlich
apt-get install -y -q --no-install-recommends python3-mpd2 2>/dev/null \
    || apt-get install -y -q --no-install-recommends python3-mpd 2>/dev/null \
    || { apt-get install -y -q python3-pip && pip3 install --break-system-packages python-mpd2; }

say "Benutzer und Ordner anlegen"
id klangkiste >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin klangkiste
for g in audio gpio spi; do getent group "$g" >/dev/null && usermod -aG "$g" klangkiste; done
install -d -o klangkiste -g klangkiste -m 755 "$DATA" "$DATA/music"

say "Programm holen"
if [ -d "$APP/.git" ]; then
    sudo -u klangkiste git -C "$APP" pull --ff-only || true
else
    rm -rf "$APP"
    git clone --depth 1 "$REPO_URL" "$APP"
    chown -R klangkiste:klangkiste "$APP"
fi

say "Soundkarte und Kartenleser einschalten"
CFG=/boot/firmware/config.txt; [ -f "$CFG" ] || CFG=/boot/config.txt
sed -i 's/^dtparam=audio=on/#dtparam=audio=on/' "$CFG"
grep -q '^dtparam=spi=on' "$CFG" || echo 'dtparam=spi=on' >> "$CFG"
grep -q "^dtoverlay=$SOUND_OVERLAY" "$CFG" || echo "dtoverlay=$SOUND_OVERLAY" >> "$CFG"
sed "s/CARDNAME/$SOUND_CARD/g" "$APP/system/asound.conf" > /etc/asound.conf

say "Abspieler (MPD) einrichten"
install -m 644 "$APP/system/mpd.conf" /etc/mpd.conf
systemctl enable mpd.service
# Erst danach maskieren: "enable mpd.service" schaltet den Socket sonst wieder ein,
# und über den Socket wäre MPD im ganzen Heimnetz erreichbar.
systemctl mask --now mpd.socket 2>/dev/null || true
systemctl restart mpd.service || true

say "Protokolle begrenzen (die alte Box lief damit voll)"
install -d /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=50M\nRuntimeMaxUse=20M\n' > /etc/systemd/journald.conf.d/klangkiste.conf
systemctl restart systemd-journald

say "Dienst einrichten"
cat > /etc/sudoers.d/klangkiste <<'EOF'
klangkiste ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff, /usr/bin/systemctl restart klangkiste
EOF
chmod 440 /etc/sudoers.d/klangkiste
install -m 644 "$APP/system/klangkiste.service" /etc/systemd/system/klangkiste.service
systemctl daemon-reload
systemctl enable klangkiste.service
systemctl restart klangkiste.service

if [ "$SPOTIFY" = 1 ]; then
    say "Spotify Connect einrichten"
    if command -v librespot >/dev/null || curl -fsSL https://dtcooper.github.io/raspotify/install.sh | sh; then
        CONF=/etc/raspotify/conf
        if [ -f "$CONF" ] && ! grep -q '^# Klangkiste' "$CONF"; then
            cat >> "$CONF" <<EOF

# Klangkiste
LIBRESPOT_NAME="Klangkiste"
LIBRESPOT_DEVICE="default"
LIBRESPOT_BITRATE="160"
LIBRESPOT_INITIAL_VOLUME="50"
LIBRESPOT_ONEVENT="$APP/system/spotify-event.sh"
EOF
        fi
        systemctl restart raspotify 2>/dev/null || true
    else
        echo "Spotify Connect ließ sich nicht installieren – die Box läuft auch ohne."
    fi
fi

IP=$(hostname -I | awk '{print $1}')
say "Fertig!"
echo "Bitte jetzt einmal neu starten:   sudo reboot"
echo "Danach im Browser öffnen:         http://$IP   oder   http://$(hostname).local"
