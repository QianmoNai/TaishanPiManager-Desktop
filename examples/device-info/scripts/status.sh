#!/bin/sh
set -eu
printf '{"uptime":"%s","load":"%s","storage":"%s"}\n' "$(uptime | sed 's/.*up \([^,]*\),.*/\1/')" "$(cut -d' ' -f1 /proc/loadavg)" "$(df -h /userdata 2>/dev/null | tail -1 | awk '{print $5}' || printf 'unknown')"
