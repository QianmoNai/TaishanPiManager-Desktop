#!/bin/sh
set -eu
printf '%s\n' '=== 网络接口 ==='
ip -br addr 2>/dev/null || ip addr
printf '%s\n' '=== 默认路由 ==='
ip route 2>/dev/null || true
