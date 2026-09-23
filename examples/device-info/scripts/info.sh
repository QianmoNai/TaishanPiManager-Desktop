#!/bin/sh
set -eu
printf '\n== System ==\n'
uname -a
printf '\n== Uptime ==\n'
uptime
printf '\n== Storage ==\n'
df -h
