#!/bin/sh
set -eu

printf '%s\n' '=== 插件导入测试：只读设备信息 ==='
printf '\n%s\n' '--- 主机名 ---'
hostname 2>/dev/null || true
printf '\n%s\n' '--- 内核 ---'
uname -a
printf '\n%s\n' '--- 运行时间 ---'
uptime
printf '\n%s\n' '--- userdata 存储 ---'
df -h /userdata 2>/dev/null || true
