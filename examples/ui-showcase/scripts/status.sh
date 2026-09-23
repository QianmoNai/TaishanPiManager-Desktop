#!/bin/sh
set -eu
hostname_value=$(hostname 2>/dev/null || printf 'unknown')
kernel_value=$(uname -r 2>/dev/null || printf 'unknown')
uptime_value=$(uptime 2>/dev/null | sed 's/.*up \([^,]*\),.*/\1/' || printf 'unknown')
load_value=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || printf 'unknown')
memory_value=$(free -m 2>/dev/null | awk '/^Mem:/{printf "%s/%s MiB", $3, $2}' || printf 'unknown')
storage_value=$(df -h /userdata 2>/dev/null | tail -1 | awk '{print $3 "/" $2 " (" $5 ")"}' || printf 'unknown')
ip_value=$(ip -o -4 addr show scope global 2>/dev/null | awk 'NR==1{print $4}' || printf 'unknown')
route_value=$(ip route 2>/dev/null | awk '/^default/{print $3; exit}' || printf 'unknown')
temperature_value=$(awk '{printf "%.1f C", $1 / 1000}' /sys/class/thermal/thermal_zone0/temp 2>/dev/null || printf 'unknown')
processes_value=$(ps 2>/dev/null | tail -n +2 | wc -l | tr -d ' ' || printf 'unknown')
time_value=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null || printf 'unknown')
printf '{"hostname":"%s","kernel":"%s","uptime":"%s","load":"%s","memory":"%s","storage":"%s","ip":"%s","route":"%s","temperature":"%s","processes":"%s","time":"%s","status":"正常"}\n' "$hostname_value" "$kernel_value" "$uptime_value" "$load_value" "$memory_value" "$storage_value" "$ip_value" "$route_value" "$temperature_value" "$processes_value" "$time_value"
