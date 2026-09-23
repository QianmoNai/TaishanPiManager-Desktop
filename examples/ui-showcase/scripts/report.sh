#!/bin/sh
set -eu
printf '%s\n' '=== 第三方插件 UI 展示：设备报告 ==='
hostname
uname -a
uptime
free -h 2>/dev/null || true
df -h 2>/dev/null
