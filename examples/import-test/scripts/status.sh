#!/bin/sh
set -eu

uptime_value=$(uptime 2>/dev/null | sed 's/.*up \([^,]*\),.*/\1/' || printf 'unknown')
load_value=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || printf 'unknown')
storage_value=$(df -h /userdata 2>/dev/null | tail -1 | awk '{print $5}' || printf 'unknown')

printf '{"uptime":"%s","load":"%s","storage":"%s"}\n' \
  "$uptime_value" "$load_value" "$storage_value"
