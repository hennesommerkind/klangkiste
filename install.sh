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

# Sprachausgabe für Ansagen: Pico klingt natürlicher (non-free), espeak-ng geht immer
if ! command -v pico2wave >/dev/null && ! command -v espeak-ng >/dev/null; then
    say "Sprachausgabe installieren"
    [ -n "$missing" ] || [ -n "$bt_missing" ] || [ "$UPDATE" = 0 ] || apt-get update -q
    apt-get install -y -q --no-install-recommends libttspico-utils 2>/dev/null \
        || apt-get install -y -q --no-install-recommends espeak-ng \
        || echo "Keine Sprachausgabe verfügbar – Ansagen fallen weg."
fi
# WLAN-Verwaltung durch die Klangkiste braucht polkit
if command -v nmcli >/dev/null && ! dpkg -s polkitd >/dev/null 2>&1; then
    apt-get install -y -q --no-install-recommends polkitd 2>/dev/null || true
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
MPD_CHANGED=0; GLR_CHANGED=0
if ! cmp -s "$APP/system/mpd.conf" /etc/mpd.conf; then
    install -m 644 "$APP/system/mpd.conf" /etc/mpd.conf
    MPD_CHANGED=1
fi
systemctl enable mpd.service
# Erst danach maskieren: "enable mpd.service" schaltet den Socket sonst wieder ein,
# und über den Socket wäre MPD im ganzen Heimnetz erreichbar.
systemctl mask --now mpd.socket 2>/dev/null || true
if [ "$MPD_CHANGED" = 1 ] || ! systemctl is-active -q mpd.service; then systemctl restart mpd.service || true; fi

say "Schneller starten"
# cloud-init richtet beim allerersten Start die Imager-Einstellungen ein und kostet
# danach bei jedem Start mehrere Sekunden. Abschalten, sobald es einmal fertig ist.
if [ -d /etc/cloud ] && [ -f /var/lib/cloud/instance/boot-finished ] && [ ! -f /etc/cloud/cloud-init.disabled ]; then
    touch /etc/cloud/cloud-init.disabled
    echo "cloud-init abgeschaltet"
fi
# MPD wartet laut Paket auf das Netzwerk, obwohl er nur auf der Box lauscht.
# Eigene Kopie der Dienstdatei ohne diese Abhängigkeit (und ohne den Netz-Socket).
if [ -f /usr/lib/systemd/system/mpd.service ]; then
    sed -e 's/^After=.*/After=sound.target/' -e '/^Also=/d' /usr/lib/systemd/system/mpd.service > /etc/systemd/system/mpd.service.new
    if ! cmp -s /etc/systemd/system/mpd.service.new /etc/systemd/system/mpd.service; then
        mv /etc/systemd/system/mpd.service.new /etc/systemd/system/mpd.service
        systemctl daemon-reload
    else
        rm -f /etc/systemd/system/mpd.service.new
    fi
fi
# Auf „online“ wartet nichts, was die Box braucht
systemctl disable NetworkManager-wait-online.service 2>/dev/null || true

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
        # Nur anfassen, was sich geändert hat – sonst reißt jedes Update Spotify ab.
        # Die Einstellungsdatei passt die Klangkiste selbst an (Lautsprecher/Kopfhörer).
        [ -f "$DATA/spotify/config.yml" ] \
            || { install -m 644 -o klangkiste -g klangkiste "$APP/system/go-librespot.yml" "$DATA/spotify/config.yml"; GLR_CHANGED=1; }
        cmp -s "$APP/system/go-librespot.service" /etc/systemd/system/go-librespot.service \
            || { install -m 644 "$APP/system/go-librespot.service" /etc/systemd/system/go-librespot.service; GLR_CHANGED=1; }
    fi
fi

say "Bluetooth für Kopfhörer einrichten"
if command -v bluetoothctl >/dev/null; then
    command -v rfkill >/dev/null && rfkill unblock bluetooth || true
    systemctl enable --now bluetooth.service 2>/dev/null || true
    BA=$(command -v bluealsad || command -v bluealsa || true)   # ab bluez-alsa 5.0 heißt das Programm bluealsad
    if [ -n "$BA" ] && systemctl cat bluealsa.service >/dev/null 2>&1; then
        install -d /etc/systemd/system/bluealsa.service.d
        BACONF=$(printf '[Service]\nExecStart=\nExecStart=%s -S -p a2dp-source\n' "$BA")
        if [ "$(cat /etc/systemd/system/bluealsa.service.d/klangkiste.conf 2>/dev/null)" != "$BACONF" ]; then
            echo "$BACONF" > /etc/systemd/system/bluealsa.service.d/klangkiste.conf
            systemctl daemon-reload
            systemctl restart bluealsa.service || true
        fi
        systemctl enable bluealsa.service 2>/dev/null || true
        systemctl is-active -q bluealsa.service || systemctl start bluealsa.service || true
    else
        echo "bluez-alsa nicht gefunden – Kopfhörer werden nicht unterstützt."
    fi
fi

say "WLAN-Verwaltung freigeben"
if command -v nmcli >/dev/null; then
    # Stromsparen des WLAN-Chips aus: Im Schlaf verpasst er Anfragen (vor allem die
    # Namenssuche „klangkiste.local“), die Box wirkt dann zeitweise unerreichbar.
    PSCONF=$(printf '[connection]\nwifi.powersave = 2\n')
    if [ "$(cat /etc/NetworkManager/conf.d/klangkiste-wlan.conf 2>/dev/null)" != "$PSCONF" ]; then
        echo "$PSCONF" > /etc/NetworkManager/conf.d/klangkiste-wlan.conf   # gilt ab dem nächsten Verbinden
    fi
    command -v iw >/dev/null && iw dev wlan0 set power_save off 2>/dev/null || true   # sofort
    install -d /etc/polkit-1/rules.d
    cat > /etc/polkit-1/rules.d/50-klangkiste.rules <<'EOF'
// Die Klangkiste darf WLANs verwalten (Netz wechseln, eigenes Not-WLAN öffnen).
polkit.addRule(function (action, subject) {
    if (action.id.indexOf("org.freedesktop.NetworkManager.") === 0 && subject.user === "klangkiste") {
        return polkit.Result.YES;
    }
});
EOF
else
    echo "NetworkManager nicht gefunden – das eigene Not-WLAN steht nicht zur Verfügung."
fi

say "Lämpchen der Platine freigeben"
# Die Klangkiste darf die rote und grüne LED des Raspberry Pi schalten (Einstellungen › System).
LEDRULE='SUBSYSTEM=="leds", KERNEL=="ACT|PWR|led0|led1", ACTION=="add|change", RUN+="/bin/sh -c '"'"'chgrp klangkiste /sys%p/trigger /sys%p/brightness; chmod g+w /sys%p/trigger /sys%p/brightness'"'"'"'
if [ "$(cat /etc/udev/rules.d/60-klangkiste-leds.rules 2>/dev/null)" != "$LEDRULE" ]; then
    echo "$LEDRULE" > /etc/udev/rules.d/60-klangkiste-leds.rules
    udevadm control --reload 2>/dev/null || true
fi
udevadm trigger --subsystem-match=leds --action=change 2>/dev/null || true
udevadm settle --timeout=5 2>/dev/null || true

say "Netzlaufwerk vorbereiten"
# Samba nur installieren; eingeschaltet wird die Freigabe erst in den Einstellungen.
if ! command -v smbd >/dev/null; then
    apt-get update -qq || true
    if DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends samba >/dev/null; then
        systemctl disable --now smbd >/dev/null 2>&1 || true
    else
        echo "Hinweis: Samba ließ sich nicht installieren – das Netzlaufwerk steht nicht zur Verfügung"
    fi
fi
if command -v smbd >/dev/null; then
    systemctl disable --now nmbd samba-ad-dc >/dev/null 2>&1 || true
    if ! cmp -s "$APP/system/smb.conf" /etc/samba/smb.conf; then
        [ -f /etc/samba/smb.conf.original ] || cp -a /etc/samba/smb.conf /etc/samba/smb.conf.original 2>/dev/null || true
        install -m 644 "$APP/system/smb.conf" /etc/samba/smb.conf
        systemctl is-active -q smbd && systemctl restart smbd || true
    fi
fi

say "Dienste einrichten"
cat > /etc/sudoers.d/klangkiste <<'EOF'
klangkiste ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff, /usr/bin/systemctl restart klangkiste, /usr/bin/systemctl restart go-librespot, /usr/bin/systemctl start --no-block klangkiste-update.service, /opt/klangkiste/system/share.sh
EOF
chmod 440 /etc/sudoers.d/klangkiste
install -m 644 "$APP/system/klangkiste.service" /etc/systemd/system/klangkiste.service
install -m 644 "$APP/system/klangkiste-update.service" /etc/systemd/system/klangkiste-update.service
install -m 644 "$APP/system/klangkiste-monitor.service" /etc/systemd/system/klangkiste-monitor.service
systemctl daemon-reload
systemctl enable klangkiste.service
systemctl enable klangkiste-monitor.service
systemctl restart klangkiste-monitor.service || true
if [ -f /etc/systemd/system/go-librespot.service ]; then
    systemctl enable go-librespot.service
    if [ "$GLR_CHANGED" = 1 ] || ! systemctl is-active -q go-librespot.service; then
        systemctl restart go-librespot.service || true
    fi
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
