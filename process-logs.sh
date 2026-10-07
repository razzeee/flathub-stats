#!/bin/bash

set -euo pipefail

LOGDIR=${1:?Usage: process-logs.sh LOGDIR STATSDIR}
STATSDIR=${2:?Usage: process-logs.sh LOGDIR STATSDIR}
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

# Serialize rotation as well as ingestion and archival.
exec 9>"$LOGDIR/.process-logs.lock"
flock -x 9

rotated=false
while IFS= read -r -d '' LOG; do
    ROTATED="$LOG.rotated"
    # Preserve both pending and archived rotations when a pathname is reused.
    if [[ -e "$ROTATED" || -e "$ROTATED.xz" ]]; then
        ROTATED=$(mktemp "${LOG%.log}.XXXXXXXX.log.rotated")
    fi
    echo "Rotating log $LOG"
    mv -- "$LOG" "$ROTATED"
    rotated=true
done < <(find "$LOGDIR" -type f -name '*.log' -mmin +240 -print0)
if [[ $EUID -eq 0 && $rotated == true ]]; then
    systemctl kill -s HUP --kill-who=main rsyslog.service
fi

mapfile -d '' -t logs < <(find "$LOGDIR" -type f -name '*.log.rotated' -print0)
if (( ${#logs[@]} == 0 )); then
    exit 0
fi

# Resolve metadata across the whole batch before counting any of its files.
"${PYTHON:-python3}" "$SCRIPT_DIR/update-stats.py" --dest "$STATSDIR" "${logs[@]}"
for LOG in "${logs[@]}"; do
    if [[ -e "$LOG.xz" ]]; then
        ARCHIVE_INPUT=$(mktemp "${LOG%.log.rotated}.XXXXXXXX.log.rotated")
        mv -- "$LOG" "$ARCHIVE_INPUT"
        LOG=$ARCHIVE_INPUT
    fi
    xz -- "$LOG"
done
