"""Safe GPIO/I2C/SPI/PWM inspection and control for TaishanPi."""
from __future__ import annotations

import re
from adb_core import UserError


def _int(value, name, low, high):
    try:
        number = int(str(value), 10)
    except (TypeError, ValueError):
        raise UserError(f'{name} 必须是数字。')
    if not low <= number <= high:
        raise UserError(f'{name} 范围为 {low}–{high}。')
    return number


def _chip(value):
    value = str(value or '').strip()
    if not re.fullmatch(r'pwmchip\d+', value):
        raise UserError('PWM 芯片格式应为 pwmchipN。')
    return value


def _bus(value):
    value = str(value or '').strip()
    match = re.fullmatch(r'i2c-(\d+)', value)
    if not match:
        raise UserError('I2C 总线格式应为 i2c-N。')
    return value, int(match.group(1))


def _decode(raw):
    return raw.decode('utf-8', 'replace') if isinstance(raw, (bytes, bytearray)) else str(raw or '')


def inventory(adb, serial):
    script = r'''
printf '__MODEL__\n'; cat /proc/device-tree/model 2>/dev/null | tr -d '\000'; printf '\n'
printf '__GPIO__\n'; cat /sys/kernel/debug/gpio 2>/dev/null || true
printf '__GPIOCHIPS__\n'; for d in /sys/class/gpio/gpiochip*; do [ -e "$d" ] || continue; printf '%s\n' "$(basename "$d")"; done
printf '__I2C__\n'; for d in /dev/i2c-*; do [ -e "$d" ] || continue; printf '%s\n' "$(basename "$d")"; done
printf '__I2CDEV__\n'; for d in /sys/class/i2c-dev/i2c-*; do [ -e "$d" ] || continue; printf '%s\n' "$(basename "$d")"; done
printf '__SPI__\n'; for d in /dev/spidev*; do [ -e "$d" ] || continue; printf '%s\n' "$(basename "$d")"; done
printf '__PWM__\n'; for d in /sys/class/pwm/pwmchip*; do [ -e "$d" ] || continue; printf '%s\n' "$(basename "$d")"; done
printf '__PINMUX__\n'; for f in /sys/kernel/debug/pinctrl/*/pinmux-pins; do [ -r "$f" ] || continue; printf '## %s\n' "$f"; sed -n '1,160p' "$f"; done
'''
    out, _, _ = adb.shell(serial, script, timeout=15)
    text = _decode(out).replace('\r\n', '\n')
    sections = {}
    current = None
    lines = []
    for line in text.splitlines():
        if line.startswith('__') and line.endswith('__'):
            if current is not None:
                sections[current] = lines
            current = line.strip('_').lower()
            lines = []
        elif current is not None:
            lines.append(line)
    if current is not None:
        sections[current] = lines
    model = '未知设备'
    for line in sections.get('model', []):
        if line.strip():
            model = line.strip()
            break
    return {
        'model': model,
        'gpio': sections.get('gpio', []),
        'gpiochips': [x for x in sections.get('gpiochips', []) if x],
        'i2c': [x for x in sections.get('i2c', []) if x],
        'i2cdev': [x for x in sections.get('i2cdev', []) if x],
        'spi': [x for x in sections.get('spi', []) if x],
        'pwm': [x for x in sections.get('pwm', []) if x],
        'pinmux': sections.get('pinmux', []),
    }


def gpio(adb, serial, number, action='read', value=None):
    number = _int(number, 'GPIO 编号', 0, 511)
    action = str(action or 'read').lower()
    if action not in {'read', 'input', 'output', 'write'}:
        raise UserError('GPIO 操作只支持 read、input、output、write。')
    if action == 'write':
        value = _int(value, 'GPIO 电平', 0, 1)
    gpio = f'/sys/class/gpio/gpio{number}'
    commands = [f'base={gpio}', 'if [ ! -d "$base" ] && [ -w /sys/class/gpio/export ]; then printf "%s" '+str(number)+' > /sys/class/gpio/export 2>/dev/null || true; fi', 'test -d "$base" || { echo GPIO_NOT_EXPORTED; exit 3; }']
    if action == 'input':
        commands.append('printf input > "$base/direction"')
    elif action in {'output', 'write'}:
        commands.append('printf output > "$base/direction"')
    if action == 'write':
        commands.append(f'printf "%s" {value} > "$base/value"')
    commands += ['printf "direction=%s\\n" "$(cat "$base/direction" 2>/dev/null || echo unknown)"', 'printf "value=%s\\n" "$(cat "$base/value" 2>/dev/null || echo unknown)"']
    out, _, code = adb.shell(serial, '\n'.join(commands), timeout=8, check=False)
    text = _decode(out)
    if code:
        if 'GPIO_NOT_EXPORTED' in text:
            raise UserError(f'GPIO {number} 不可用或未导出。')
        raise UserError(text.strip() or f'GPIO {number} 操作失败。')
    result = {'number': number, 'direction': 'unknown', 'value': 'unknown'}
    for key in ('direction', 'value'):
        match = re.search(rf'^{key}=([^\r\n]*)', text, re.M)
        if match:
            result[key] = match.group(1).strip()
    return result


def i2c_scan(adb, serial, bus):
    bus, number = _bus(bus)
    probe = 'command -v i2cdetect >/dev/null 2>&1 || { echo I2CDETECT_MISSING; exit 4; }; i2cdetect -y '+str(number)
    out, err, code = adb.shell(serial, probe, timeout=20, check=False)
    text = _decode(out)+_decode(err)
    if code:
        if 'I2CDETECT_MISSING' in text:
            raise UserError('设备没有安装 i2cdetect，暂时只能查看 I2C 设备节点。')
        raise UserError(text.strip() or f'{bus} 扫描失败。')
    return {'bus': bus, 'output': _decode(out).strip()}


def pwm(adb, serial, chip, channel, action='status', period=None, duty=None, enable=None):
    chip = _chip(chip)
    channel = _int(channel, 'PWM 通道', 0, 31)
    action = str(action or 'status').lower()
    if action not in {'status', 'configure'}:
        raise UserError('PWM 操作只支持 status、configure。')
    if action == 'configure':
        period = _int(period, '周期（纳秒）', 1, 1_000_000_000)
        duty = _int(duty, '占空比（纳秒）', 0, period)
        enable_value = 1 if bool(enable) else 0
    path = f'/sys/class/pwm/{chip}/pwm{channel}'
    lines = [f'base={path}', 'test -d "$base" || { echo PWM_NOT_EXPORTED; exit 3; }']
    if action == 'configure':
        lines += [f'printf "%s" {period} > "$base/period"', f'printf "%s" {duty} > "$base/duty_cycle"', f'printf "%s" {enable_value} > "$base/enable"']
    lines += ['printf "period=%s\\n" "$(cat "$base/period" 2>/dev/null || echo unknown)"', 'printf "duty=%s\\n" "$(cat "$base/duty_cycle" 2>/dev/null || echo unknown)"', 'printf "enable=%s\\n" "$(cat "$base/enable" 2>/dev/null || echo unknown)"']
    out, err, code = adb.shell(serial, '\n'.join(lines), timeout=8, check=False)
    text = _decode(out)+_decode(err)
    if code:
        if 'PWM_NOT_EXPORTED' in text:
            raise UserError(f'{chip} 通道 {channel} 尚未导出。')
        raise UserError(text.strip() or 'PWM 操作失败。')
    result = {'chip': chip, 'channel': channel, 'period': 'unknown', 'duty': 'unknown', 'enable': 'unknown'}
    for key in ('period', 'duty', 'enable'):
        match = re.search(rf'^{key}=([^\r\n]*)', text, re.M)
        if match:
            result[key] = match.group(1).strip()
    return result


def dispatch(adb, serial, action, data):
    if action == 'inventory':
        return inventory(adb, serial)
    if action == 'gpio':
        return gpio(adb, serial, data.get('number'), data.get('action', 'read'), data.get('value'))
    if action == 'i2c-scan':
        return i2c_scan(adb, serial, data.get('bus'))
    if action == 'pwm':
        return pwm(adb, serial, data.get('chip'), data.get('channel'), data.get('action', 'status'), data.get('period'), data.get('duty'), data.get('enable'))
    raise UserError('不支持的引脚助手操作。')
