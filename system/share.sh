#!/bin/bash
# Netzlaufwerk ein-/ausschalten. Läuft per sudo als root; die Klangkiste darf nur genau das.
#   share.sh on     Kennwort (mind. 8 Zeichen) auf der Standardeingabe; leer = altes behalten
#   share.sh off
set -u
case "${1:-}" in
    on)
        IFS= read -r pw || true
        if [ -n "$pw" ]; then
            [ ${#pw} -ge 8 ] || { echo "Das Kennwort braucht mindestens 8 Zeichen"; exit 2; }
            printf '%s\n%s\n' "$pw" "$pw" | smbpasswd -s -a klangkiste >/dev/null || { echo "Kennwort ließ sich nicht setzen"; exit 3; }
        elif ! pdbedit -L 2>/dev/null | grep -q '^klangkiste:'; then
            echo "Bitte ein Kennwort vergeben"; exit 2
        fi
        systemctl enable --now smbd >/dev/null 2>&1 || { echo "Der Freigabe-Dienst ließ sich nicht starten"; exit 4; }
        ;;
    off)
        systemctl disable --now smbd >/dev/null 2>&1 || true
        ;;
    *) echo "unbekannter Aufruf"; exit 1 ;;
esac
