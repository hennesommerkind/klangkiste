#!/bin/bash
# Stellt die gespeicherten Netzwerkverbindungen von netplan auf normale
# NetworkManager-Dateien um. Hintergrund: Mit netplan ruft NetworkManager beim
# Start mehrfach „netplan generate“ auf (auf dem Pi 3 je ~7 s).
#
#   sudo bash /opt/klangkiste/system/netz-ohne-netplan.sh          umstellen
#   sudo bash /opt/klangkiste/system/netz-ohne-netplan.sh --undo   zurück zu netplan
#
# Wirksam nach dem nächsten Neustart. Geht danach gar nichts, öffnet die
# Klangkiste nach einer Minute ihr eigenes WLAN – dort das Heim-WLAN neu eintragen
# oder per SSH (10.42.0.1) „--undo“ ausführen.
set -euo pipefail

BK=/var/lib/klangkiste/netplan-sicherung
DST=/etc/NetworkManager/system-connections
RUN=/run/NetworkManager/system-connections

[ "$(id -u)" = 0 ] || { echo "Bitte mit sudo starten."; exit 1; }

if [ "${1:-}" = "--undo" ]; then
    ls "$BK"/90-NM-*.yaml >/dev/null 2>&1 || { echo "Keine Sicherung in $BK gefunden."; exit 1; }
    cp -a "$BK"/90-NM-*.yaml /etc/netplan/
    if [ -f "$BK/angelegt.txt" ]; then
        while read -r f; do rm -f -- "$f"; done < "$BK/angelegt.txt"
    fi
    rm -rf "$BK"
    echo "netplan-Dateien wiederhergestellt. Jetzt neu starten: sudo reboot"
    exit 0
fi

shopt -s nullglob
yamls=(/etc/netplan/90-NM-*.yaml)
if [ ${#yamls[@]} -eq 0 ]; then
    echo "Nichts zu tun: keine von NetworkManager angelegten netplan-Dateien gefunden."
    exit 0
fi

# Erst alles prüfen, dann erst etwas ändern.
declare -A ziel
for y in "${yamls[@]}"; do
    uuid=$(basename "$y" .yaml); uuid=${uuid#90-NM-}
    src=$(grep -lx "uuid=$uuid" "$RUN"/*.nmconnection 2>/dev/null | head -1 || true)
    if [ -z "$src" ]; then
        echo "Abbruch: zu $(basename "$y") gibt es keine laufende Verbindung in $RUN."
        exit 1
    fi
    if grep -qx "type=wifi" "$src" && grep -q "^key-mgmt=wpa-psk" "$src" && ! grep -q "^psk=" "$src"; then
        echo "Abbruch: In $(basename "$src") fehlt das WLAN-Kennwort."
        exit 1
    fi
    name=$(sed -n 's/^id=//p' "$src" | head -1)
    name=${name#netplan-}; name=${name#wlan0-}; name=${name#eth0-}
    [ -n "$name" ] || name=$uuid
    out="$DST/$(printf '%s' "$name" | tr -c 'A-Za-z0-9._-' '_').nmconnection"
    [ -e "$out" ] && { echo "Abbruch: $out gibt es schon."; exit 1; }
    ziel[$y]="$src|$out|$name"
done

mkdir -p "$BK"; chmod 700 "$BK"
: > "$BK/angelegt.txt"
for y in "${yamls[@]}"; do
    IFS='|' read -r src out name <<< "${ziel[$y]}"
    install -m 600 -o root -g root "$src" "$out"
    sed -i "s/^id=.*/id=$(printf '%s' "$name" | sed 's/[\/&]/\\&/g')/" "$out"
    echo "$out" >> "$BK/angelegt.txt"
    mv "$y" "$BK/"
    echo "Umgestellt: $name  →  $out"
done

echo
echo "Fertig. Wirksam nach dem Neustart: sudo reboot"
echo "Danach prüfen:  nmcli -f NAME,FILENAME connection show"
echo "Zurück:         sudo bash $0 --undo && sudo reboot"
