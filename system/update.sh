#!/bin/bash
# Wird vom Update-Knopf über klangkiste-update.service (als root) gestartet.
set -euo pipefail

main() {
    LOG=/var/lib/klangkiste/update.log
    exec > >(tee "$LOG") 2>&1
    echo "Update gestartet: $(date '+%d.%m.%Y %H:%M')"
    git -C /opt/klangkiste fetch --depth 50 origin main
    OLD=$(git -C /opt/klangkiste rev-parse --short HEAD)
    git -C /opt/klangkiste reset --hard origin/main
    NEW=$(git -C /opt/klangkiste rev-parse --short HEAD)
    echo "Version $OLD → $NEW"
    bash /opt/klangkiste/install.sh --update
    echo "Update fertig: $(date '+%d.%m.%Y %H:%M')"
}

# main() zuerst vollständig einlesen – git ersetzt diese Datei währenddessen.
main "$@"
exit
