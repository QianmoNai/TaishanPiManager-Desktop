"""Board RGB LED control through the Linux LED class."""
import re

from adb_core import UserError

LED_PATHS = {'r': '/sys/class/leds/rgb-led-r', 'g': '/sys/class/leds/rgb-led-g', 'b': '/sys/class/leds/rgb-led-b'}


def status(adb, serial):
    script = """
for c in r g b; do
  d=/sys/class/leds/rgb-led-$c
  test -e "$d/brightness" || { echo "$c=-1"; continue; }
  printf '%s=%s\\n' "$c" "$(cat "$d/brightness" 2>/dev/null || echo -1)"
done
"""
    out, _, _ = adb.shell(serial, script)
    values = {m[1]: int(m[2]) for m in re.finditer(r'([rgb])=(-?\d+)', out.decode('utf-8', 'replace'))}
    if any(values.get(c, -1) < 0 for c in 'rgb'):
        raise UserError('当前设备未发现板载 RGB 灯控制节点。')
    return {'r': values.get('r', 0), 'g': values.get('g', 0), 'b': values.get('b', 0)}


def set_color(adb, serial, data):
    values = {c: data.get(c, 0) for c in 'rgb'}
    if any(value not in (0, 1) for value in values.values()):
        raise UserError('RGB 通道只支持 0（灭）或 1（亮）。')
    script = """
for c in r g b; do
  d=/sys/class/leds/rgb-led-$c
  test -e "$d/brightness" || exit 2
  echo none > "$d/trigger" 2>/dev/null || true
done
""" + ''.join(f"echo {values[c]} > /sys/class/leds/rgb-led-{c}/brightness\n" for c in 'rgb')
    adb.shell(serial, script)
    return {'r': values['r'], 'g': values['g'], 'b': values['b']}
