#!/bin/sh
# Wird von librespot (Spotify Connect) bei jedem Ereignis aufgerufen und
# meldet es der Klangkiste, damit die Karten-Wiedergabe pausiert.
curl -s -m 3 -d "event=${PLAYER_EVENT}" http://127.0.0.1/api/hook/spotify >/dev/null 2>&1 || true
