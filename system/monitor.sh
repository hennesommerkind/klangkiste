#!/bin/bash
# Schreibt alle 15 s eine Zeile: Ist das WLAN da? Antwortet die Oberfläche? Damit lässt sich
# hinterher sagen, auf welcher Ebene ein Aussetzer lag. Auswertung: diagnose.sh
LOG=/var/lib/klangkiste/monitor.log
n=0
while true; do
    gw=$(ip route 2>/dev/null | awk '/^default/{print $3; exit}')
    if [ -n "$gw" ]; then
        p=$(ping -n -c1 -W2 "$gw" 2>/dev/null | sed -n 's/.*time=\([0-9.]*\).*/\1/p'); p=${p:-FEHLT}
    else
        p=KEIN-NETZ
    fi
    h=$(curl -s -o /dev/null -m 5 -w '%{time_total}' http://127.0.0.1/api/state 2>/dev/null) || h=FEHLT
    sig=$(iw dev wlan0 link 2>/dev/null | sed -n 's/.*signal: \(-[0-9]*\).*/\1/p')
    thr=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
    bt=$(pgrep -fc "bluetoothctl.* connect" 2>/dev/null || true)
    echo "$(date '+%d.%m. %H:%M:%S') router=$p seite=$h signal=${sig:--} strom=${thr:--} last=$(cut -d' ' -f1 /proc/loadavg) bt-suche=${bt:-0}" >> "$LOG"
    n=$((n + 1))
    if [ $((n % 500)) = 0 ] && [ "$(wc -l < "$LOG")" -gt 12000 ]; then   # gut zwei Tage behalten
        tail -n 10000 "$LOG" > "$LOG.neu" && mv "$LOG.neu" "$LOG"
    fi
    sleep 15
done
