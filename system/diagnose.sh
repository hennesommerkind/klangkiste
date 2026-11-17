#!/bin/bash
# Sammelt alles, was bei „die Box ist schlecht erreichbar“ hilft. Gibt keine Kennwörter aus.
#   sudo bash /opt/klangkiste/system/diagnose.sh
h() { printf '\n=== %s ===\n' "$*"; }
h "System"; uptime; cat /proc/device-tree/model 2>/dev/null; echo
free -m | sed -n 1,3p
echo "Temperatur: $(( $(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo 0) / 1000 )) °C"
# 0x0 = alles gut; sonst Unterspannung/Drosselung (auch in der Vergangenheit)
command -v vcgencmd >/dev/null && vcgencmd get_throttled
h "Dienste"
for s in klangkiste mpd go-librespot NetworkManager avahi-daemon bluealsa; do
    printf '%-16s %-9s Neustarts: %s\n' "$s" "$(systemctl is-active $s 2>/dev/null)" "$(systemctl show -p NRestarts --value $s 2>/dev/null)"
done
h "Klangkiste: Start und Fehler seit dem Einschalten"
journalctl -b -u klangkiste --no-pager -o short-precise 2>/dev/null | grep -E "Started|Stopping|Stopped|Running on|ERROR|Traceback|Error|killed|timed out" | tail -25
h "Letztes Update"; tail -5 /var/lib/klangkiste/update.log 2>/dev/null
h "WLAN"
command -v iw >/dev/null && { iw dev wlan0 get power_save; iw dev wlan0 link | grep -E "SSID|freq|signal|bitrate"; }
nmcli -t -f GENERAL.STATE,IP4.ADDRESS,IP6.ADDRESS device show wlan0 2>/dev/null | cat
h "WLAN-Abbrüche und Treiberfehler seit dem Einschalten"
journalctl -b --no-pager 2>/dev/null | grep -ciE "wlan0.*(disconnect|deauth|link is not ready)" | sed 's/^/Trennungen: /'
journalctl -b -k --no-pager 2>/dev/null | grep -iE "brcmf|mmc1|voltage|oom" | tail -12
h "Name im Netz"
hostname; systemctl is-active avahi-daemon
grep -E "^(use-ipv|publish-aaaa|allow-interfaces|deny-interfaces)" /etc/avahi/avahi-daemon.conf 2>/dev/null
command -v avahi-resolve >/dev/null && timeout 5 avahi-resolve -n "$(hostname).local" 2>&1
h "Oberfläche"
ss -ltnp 2>/dev/null | grep -E ":80 |:8000 " | awk '{print $4, $6}'
curl -s -o /dev/null -w "Antwort %{http_code} in %{time_total}s\n" http://127.0.0.1/api/state
h "Aussetzer laut Mitschrift (alle 15 s eine Messung)"
M=/var/lib/klangkiste/monitor.log
if [ -f "$M" ]; then
    echo "Messungen: $(wc -l < "$M"), seit $(head -1 "$M" | cut -d' ' -f1,2)"
    echo "Router nicht erreicht:      $(grep -c 'router=\(FEHLT\|KEIN-NETZ\)' "$M")"
    echo "Router langsam (> 200 ms):  $(awk '{for(i=1;i<=NF;i++) if($i ~ /^router=[0-9.]+$/){split($i,a,"="); if(a[2]>200) c++}} END{print c+0}' "$M")"
    echo "Oberfläche ohne Antwort:    $(grep -c 'seite=FEHLT' "$M")"
    echo "Oberfläche langsam (> 1 s): $(awk '{for(i=1;i<=NF;i++) if($i ~ /^seite=[0-9.]+$/){split($i,a,"="); if(a[2]>1) c++}} END{print c+0}' "$M")"
    echo "Unterspannung gerade:       $(grep -c 'strom=0x[0-9a-f]*[1357]$\|strom=0x[0-9a-f]*[1357] ' "$M")"
    echo "Während Bluetooth-Suche:    $(grep -c 'bt-suche=[1-9]' "$M")"
    echo "--- die letzten 25 auffälligen Messungen ---"
    awk '{bad=0; for(i=1;i<=NF;i++){ if($i ~ /^router=(FEHLT|KEIN-NETZ)$/ || $i=="seite=FEHLT") bad=1;
          if($i ~ /^router=[0-9.]+$/){split($i,a,"="); if(a[2]>200) bad=1}
          if($i ~ /^seite=[0-9.]+$/){split($i,a,"="); if(a[2]>1) bad=1} } if(bad) print}' "$M" | tail -25
else
    echo "Noch keine Mitschrift – erst nach dem Update vorhanden."
fi
h "Bluetooth"
grep -o '"device": *"[^"]*"\|"auto_connect": *[a-z]*' /var/lib/klangkiste/config.json 2>/dev/null | sed 's/"device": *"\(..\).*"/gemerkter Kopfhörer: ja (\1:…)/; s/"device": *""/gemerkter Kopfhörer: nein/'
journalctl -b -u klangkiste --no-pager 2>/dev/null | grep -c "Kopfhörer" | sed 's/^/Kopfhörer-Meldungen: /'
