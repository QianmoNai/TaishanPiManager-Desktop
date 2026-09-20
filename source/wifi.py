"""Wi-Fi control for TaishanPi Buildroot using its wpa_supplicant control socket."""
import hashlib
import re
import shlex

from adb_core import UserError


def decode_ssid(value):
    """Decode wpa_cli's printf_encode output while preserving the SSID's bytes."""
    result = bytearray(); i = 0
    escapes = {'n': 10, 'r': 13, 't': 9, 'e': 27, '\\': 92, '"': 34}
    while i < len(value):
        if value[i] == '\\' and i + 1 < len(value):
            if value[i+1] == 'x' and re.fullmatch('[0-9a-fA-F]{2}', value[i+2:i+4]):
                result.append(int(value[i+2:i+4], 16)); i += 4; continue
            if value[i+1] in escapes:
                result.append(escapes[value[i+1]]); i += 2; continue
        result.extend(value[i].encode('utf-8')); i += 1
    return bytes(result)


def security(flags):
    if 'EAP' in flags: return 'enterprise', '企业认证（暂不支持）'
    if 'PSK' in flags: return 'psk', 'WPA/WPA2 个人'
    if 'SAE' in flags: return 'unsupported', 'WPA3（暂不支持）'
    if any(x in flags for x in ('WEP', 'OWE', 'RSN', 'WPA')): return 'unsupported', '加密方式暂不支持'
    return 'open', '开放网络'


def parse_scan(raw):
    networks = []
    for line in raw.splitlines():
        fields = line.split('\t', 4)
        if len(fields) != 5 or not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', fields[0]): continue
        try: frequency, signal = int(fields[1]), int(fields[2])
        except ValueError: continue
        ssid = decode_ssid(fields[4]); mode, description = security(fields[3])
        networks.append({'bssid': fields[0].lower(), 'ssid': ssid.decode('utf-8', 'replace'),
                         'ssid_hex': ssid.hex(), 'signal': signal, 'frequency': frequency,
                         'security': mode, 'security_label': description, 'flags': fields[3]})
    return sorted(networks, key=lambda item: item['signal'], reverse=True)


def parse_state(raw):
    values = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
    return {'state': values.get('wpa_state', 'UNKNOWN'),
            'ssid': decode_ssid(values.get('ssid', '')).decode('utf-8', 'replace'),
            'ip': values.get('ip_address', ''), 'bssid': values.get('bssid', ''), 'id': values.get('id', '')}


PREPARE = r'''
export LC_ALL=C
command -v wpa_cli >/dev/null 2>&1 || { echo ERR:NO_WPA_CLI; exit 1; }
interfaces=''
for dev in /sys/class/net/*; do
  [ -d "$dev/wireless" ] || grep -q '^DEVTYPE=wlan$' "$dev/uevent" 2>/dev/null || continue
  name=${dev##*/}
  case "$name" in p2p*|lo) continue;; esac
  interfaces="$interfaces $name"
done
printf '@@interfaces\n%s\n' "$interfaces"
[ -n "$interfaces" ] || { echo ERR:NO_INTERFACE; exit 1; }
if [ -z "$iface" ]; then
  for name in $interfaces; do [ -n "$iface" ] || iface=$name; [ "$name" != wlan0 ] || iface=$name; done
fi
found=0
for name in $interfaces; do [ "$iface" != "$name" ] || found=1; done
[ "$found" = 1 ] || { echo ERR:INTERFACE_CHANGED; exit 1; }
printf '@@interface\n%s\n' "$iface"
for manager in NetworkManager connmand iwd; do
  if pidof "$manager" >/dev/null 2>&1; then echo ERR:OTHER_MANAGER; exit 1; fi
done
ctrl=''
for path in /var/run/wpa_supplicant /run/wpa_supplicant /tmp/wpa_supplicant; do
  if wpa_cli -p "$path" -i "$iface" ping 2>/dev/null | grep -q '^PONG$'; then ctrl=$path; break; fi
done
if [ -z "$ctrl" ]; then
  [ "$start_allowed" = 1 ] || { echo ERR:NOT_RUNNING; exit 1; }
  if pidof wpa_supplicant >/dev/null 2>&1; then echo ERR:NO_CONTROL_SOCKET; exit 1; fi
  command -v wpa_supplicant >/dev/null 2>&1 || { echo ERR:NO_SUPPLICANT; exit 1; }
  ip link set "$iface" up >/dev/null 2>&1 || { echo ERR:RADIO_DOWN; exit 1; }
  ctrl=/var/run/wpa_supplicant
  mkdir -p "$ctrl" || exit 1
  wpa_supplicant -B -i "$iface" -C "$ctrl" >/dev/null 2>&1 || { echo ERR:START_FAILED; exit 1; }
  sleep 1
fi
cli() { wpa_cli -p "$ctrl" -i "$iface" "$@" 2>/dev/null; }
cli ping | grep -q '^PONG$' || { echo ERR:NO_CONTROL_SOCKET; exit 1; }
'''

ERRORS = {
    'NO_WPA_CLI': '固件缺少 wpa_cli，请启用 Buildroot 的 wpa_supplicant CLI。',
    'NO_INTERFACE': '未检测到无线网卡，请检查泰山派 Wi-Fi 驱动与天线。',
    'INTERFACE_CHANGED': '无线网卡已变化，请重新选择网卡并扫描。',
    'OTHER_MANAGER': '该系统使用其他网络管理服务，本版本仅支持泰山派 Buildroot 的 wpa_supplicant。',
    'NOT_RUNNING': 'Wi-Fi 服务尚未启动，请先点击“扫描附近 Wi-Fi”。',
    'NO_CONTROL_SOCKET': '无线服务正在运行但控制接口不可用，请检查 wpa_supplicant 的 ctrl_interface 配置。',
    'NO_SUPPLICANT': '固件缺少 wpa_supplicant。',
    'RADIO_DOWN': '无法启用无线网卡，请检查驱动或无线开关。',
    'START_FAILED': '无法启动无线服务，请检查驱动和板端日志。',
    'SCAN_FAILED': '无线扫描请求失败，请稍后重试。',
    'SETUP_FAILED': '无法配置选中的无线网络。',
    'AUTH_TIMEOUT': '未能完成 Wi-Fi 认证，请检查密码、信号和路由器设置。已尝试恢复原网络。',
    'CONNECTION_LOST': 'Wi-Fi 认证后连接又中断了，请检查信号并刷新状态。',
}


def sections(raw):
    return {m[1]: m[2].strip('\r\n') for m in re.finditer(r'@@(\w+)\r?\n(.*?)(?=@@\w+\r?\n|\Z)', raw, re.S)}


class Wifi:
    def __init__(self, adb, serial, interface=''):
        if not isinstance(interface, str) or (interface and not re.fullmatch(r'[a-zA-Z0-9_.-]{1,32}', interface)):
            raise UserError('无线网卡名称无效。')
        self.adb, self.serial, self.interface = adb, serial, interface

    def prepare(self, start=False):
        return f'iface={shlex.quote(self.interface)}\nstart_allowed={int(start)}\n' + PREPARE

    def execute(self, script, timeout=65):
        try: output, _, code = self.adb.shell_input(self.serial, script, timeout)
        except UserError:
            raise UserError('Wi-Fi 操作中断或超时，请检查 ADB 连接。使用网络 ADB 时，切换 Wi-Fi 可能改变地址；请通过 USB 重新连接并刷新状态。') from None
        text = output.decode('utf-8', 'replace')
        error = re.search(r'^ERR:(\w+)', text, re.M)
        if error: raise UserError(ERRORS.get(error[1], 'Wi-Fi 操作失败，请刷新连接状态。'))
        if code: raise UserError('Wi-Fi 操作失败，请检查设备连接和无线服务。')
        data = sections(text)
        state = parse_state(data.get('status', ''))
        return {**state, 'interface': data.get('interface', self.interface),
                'interfaces': data.get('interfaces', '').split(), 'raw': data}

    def status(self):
        result = self.execute(self.prepare() + '\nprintf "@@status\\n"; cli status\n', 15)
        result.pop('raw', None); return result

    def scan(self):
        script = self.prepare(True) + r'''
answer=$(cli scan)
case "$answer" in OK|FAIL-BUSY) ;; *) echo ERR:SCAN_FAILED; exit 1;; esac
sleep 3
count=0
while cli status | grep -q '^wpa_state=SCANNING$'; do
  count=$((count+1)); [ "$count" -lt 8 ] || break
  sleep 1
done
printf '@@scan\n'; cli scan_results
printf '@@status\n'; cli status
'''
        result = self.execute(script, 25)
        result['networks'] = parse_scan(result.pop('raw').get('scan', ''))
        return result

    def connect(self, data):
        ssid_hex, mode, password = data.get('ssid_hex', ''), data.get('security'), data.get('password', '')
        if not isinstance(ssid_hex, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}){1,32}', ssid_hex):
            raise UserError('请选择扫描结果中有效的 Wi-Fi 网络。')
        if mode not in ('psk', 'open'): raise UserError('暂不支持此网络的加密方式，请选择 WPA/WPA2 个人网络或开放网络。')
        if not isinstance(password, str): raise UserError('Wi-Fi 密码无效。')
        if mode == 'psk':
            encoded = password.encode('utf-8')
            if re.fullmatch(r'[0-9a-fA-F]{64}', password): psk = password.lower()
            elif 8 <= len(encoded) <= 63 and not any(ord(c) < 32 or ord(c) == 127 for c in password):
                psk = hashlib.pbkdf2_hmac('sha1', encoded, bytes.fromhex(ssid_hex), 4096, 32).hex()
            else: raise UserError('WPA/WPA2 密码须为 8–63 字节，或 64 位十六进制密钥。')
            credential = f'cli set_network "$new_id" key_mgmt WPA-PSK | grep -q "^OK$" || fail_setup\ncli set_network "$new_id" psk {psk} | grep -q "^OK$" || fail_setup\n'
        else:
            credential = 'cli set_network "$new_id" key_mgmt NONE | grep -q "^OK$" || fail_setup\n'
        script = self.prepare(True) + r'''
old_id=$(cli status | sed -n 's/^id=//p')
enabled=$(cli list_networks | awk -F '\t' 'NR>1 && $1 ~ /^[0-9]+$/ && $4 !~ /\[DISABLED\]/ {print $1}')
new_id=''; committed=0
rollback() {
  [ "$committed" = 0 ] || return
  [ -z "$new_id" ] || cli remove_network "$new_id" >/dev/null
  case "$old_id" in ''|*[!0-9]*) ;; *) cli select_network "$old_id" >/dev/null;; esac
  for id in $enabled; do cli enable_network "$id" >/dev/null; done
}
trap rollback EXIT
trap 'exit 1' INT TERM HUP
fail_setup() { echo ERR:SETUP_FAILED; exit 1; }
new_id=$(cli add_network)
case "$new_id" in ''|*[!0-9]*) new_id=''; fail_setup;; esac
''' + f'cli set_network "$new_id" ssid {ssid_hex.lower()} | grep -q "^OK$" || fail_setup\n' + credential + r'''
cli select_network "$new_id" | grep -q '^OK$' || fail_setup
authenticated=0
count=0
while [ "$count" -lt 30 ]; do
  state=$(cli status)
  if printf '%s\n' "$state" | grep -q '^wpa_state=COMPLETED$' && printf '%s\n' "$state" | grep -q "^id=$new_id$"; then authenticated=1; break; fi
  count=$((count+1)); sleep 1
done
[ "$authenticated" = 1 ] || { echo ERR:AUTH_TIMEOUT; exit 1; }
committed=1
# Existing dhcpcd usually handles link events. Ask it to refresh this interface only.
if command -v dhcpcd >/dev/null 2>&1; then
  dhcpcd -n "$iface" </dev/null >/dev/null 2>&1 &
elif command -v udhcpc >/dev/null 2>&1; then
  if ! pidof udhcpc >/dev/null 2>&1; then udhcpc -i "$iface" -n -q -t 3 -T 3 </dev/null >/dev/null 2>&1 & fi
fi
count=0
while [ "$count" -lt 12 ]; do
  cli status | grep -q '^ip_address=.' && break
  count=$((count+1)); sleep 1
done
state=$(cli status)
printf '@@status\n%s\n' "$state"
printf '%s\n' "$state" | grep -q '^wpa_state=COMPLETED$' && printf '%s\n' "$state" | grep -q "^id=$new_id$" || { echo ERR:CONNECTION_LOST; exit 1; }
printf '@@connected\nyes\n'
'''
        result = self.execute(script)
        confirmed = result.pop('raw').get('connected') == 'yes'
        if not confirmed or result['state'] != 'COMPLETED': raise UserError('连接状态尚未确认，请刷新 Wi-Fi 状态。')
        result['message'] = ('已连接 Wi-Fi，IPv4：' + result['ip']) if result['ip'] else 'Wi-Fi 已认证，尚未获取 IPv4 地址，请稍后刷新状态。'
        return result
