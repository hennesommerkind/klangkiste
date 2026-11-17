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
