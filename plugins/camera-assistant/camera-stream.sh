#!/bin/sh
child=
trap '[ -z "$child" ] || kill "$child" 2>/dev/null; wait "$child" 2>/dev/null' EXIT
trap 'exit 0' HUP INT TERM
sh -c "$1" </dev/null &
child=$!
while kill -0 "$child" 2>/dev/null; do
 read -r -t 6 heartbeat || break
 [ "$heartbeat" != stop ] || break
done
