#!/bin/sh
set -eu
printf '%s\n' '=== 最近内核日志摘要 ==='
dmesg 2>/dev/null | tail -n 30 || printf 'dmesg unavailable\n'
