"""ADB transport and device operations. No browser or HTTP server."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading

MAX_TRANSFER = 512 * 1024 * 1024
MAX_OUTPUT = 2 * 1024 * 1024
PORTABLE = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent
LOG_PATHS = {'kernel': None, 'system': '/var/log/messages', 'monitor': '/userdata/log/check_lan_monitor.log', 'autostart': '/userdata/log/check_monitor_autostart.log'}
SERVICE_CMDS = {'status': '/userdata/bin/status_check_monitor', 'start': '/userdata/bin/start_check_monitor', 'stop': '/userdata/bin/stop_check_monitor'}


class UserError(Exception):
    pass


def remote_path(value):
    if not isinstance(value, str) or not value.startswith('/') or any(ord(c) < 32 for c in value):
        raise UserError('请输入以 / 开头的 Linux 绝对路径，不支持控制字符。')
    if '..' in PurePosixPath(value).parts:
        raise UserError('路径不能包含 ..，请使用完整绝对路径。')
    return str(PurePosixPath(value))


def endpoint(value):
    value = str(value).strip()
    if not re.fullmatch(r'[A-Za-z0-9.-]+:[0-9]{1,5}', value):
        raise UserError('网络地址格式：192.168.1.150:5555')
    host, port = value.rsplit(':', 1)
    if not 1 <= int(port) <= 65535 or host.startswith('-'):
        raise UserError('网络地址或端口无效。')
    return value


class Adb:
    def __init__(self, path=None):
        candidates = [path, os.environ.get('TAISHAN_ADB'), str(PORTABLE / 'adb' / 'adb.exe'), shutil.which('adb'), r'D:\Document\platform-tools\adb.exe']
        self.path = next((str(p) for p in candidates if p and Path(p).is_file()), '')
        self.locks = {}
        self.guard = threading.Lock()

    def run(self, args, timeout=12, check=True, input_data=None):
        if not self.path:
            raise UserError('未找到 ADB。请保留软件目录中的 adb 文件夹，或设置 TAISHAN_ADB。')
        # File-backed output prevents a verbose command from exhausting RAM.
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                result = subprocess.run([self.path, *args], stdout=out, stderr=err,
                                        **({'input': input_data} if input_data is not None else {'stdin': subprocess.DEVNULL}),
                                        timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except subprocess.TimeoutExpired:
                raise UserError('ADB 操作超时，请检查设备连接。命令可能仍在设备端运行，请勿重复启动长任务。')
            except OSError as exc:
                raise UserError(f'无法启动 ADB：{exc}')
            out.seek(0); err.seek(0)
            data = out.read(MAX_OUTPUT + 1)
            error = err.read(8192).decode('utf-8', 'replace')
            if len(data) > MAX_OUTPUT:
                raise UserError('输出超过 2 MB，请缩小查询范围或使用文件下载。')
            if check and result.returncode:
                raise UserError((error or data.decode('utf-8', 'replace') or 'ADB 执行失败').strip())
            return data, error, result.returncode

    def devices(self):
        out, _, _ = self.run(['devices', '-l'])
        items = []
        for line in out.decode('utf-8', 'replace').splitlines():
            fields = line.split()
            if len(fields) < 2 or fields[0] in ('List', '*'):
                continue
            props = dict(f.split(':', 1) for f in fields[2:] if ':' in f)
            items.append({'serial': fields[0], 'state': fields[1], 'model': props.get('model', 'Linux / ADB'),
                          'transport': '网络' if ':' in fields[0] else 'USB'})
        return items

    def lock(self, serial):
        if not isinstance(serial, str) or not serial or len(serial) > 200 or re.search(r'\s', serial) or serial.startswith('-'):
            raise UserError('请先选择一个在线设备。')
        with self.guard:
            return self.locks.setdefault(serial, threading.Lock())

    def shell(self, serial, script, timeout=12, check=True):
        out, err, code = self.shell_input(serial, script, timeout)
        if check and code:
            raise UserError((err or out.decode('utf-8','replace') or '设备命令执行失败').strip())
        return out, err, code

    def shell_input(self, serial, script, timeout=65):
        # Credentials travel through stdin, never host process arguments or files.
        # Rockchip adbd exec-out differs in quoting and does not forward stdin.
        # A non-PTY shell supports stdin; our trailer preserves the remote exit code
        # even when older adbd returns host exit code 0 for failed commands.
        marker = ('__TSPI_EXIT_' + secrets.token_hex(12) + '__').encode('ascii')
        wrapped = '(\n' + script + '\n) </dev/null\nresult=$?\nprintf "\\n' + marker.decode() + '%s\\n" "$result"\nexit 0\n'
        out, err, host_code = self.run(['-s', serial, 'shell', '-T', 'sh', '-s'], timeout, False, wrapped.encode('utf-8'))
        match = re.search(rb'\r?\n' + marker + rb'(\d+)\r?\n?\Z', out)
        if not match:
            raise UserError('设备命令未返回完整结果，请检查 ADB 连接。')
        return out[:match.start()], err, int(match[1]) or host_code


STATUS_SCRIPT = r'''
printf '\n@@identity\n'; uname -srmo; hostname
printf '\n@@os\n'; cat /etc/os-release 2>/dev/null
printf '\n@@uptime\n'; cat /proc/uptime
printf '\n@@memory\n'; cat /proc/meminfo
printf '\n@@load\n'; cat /proc/loadavg
printf '\n@@cpu\n'; head -n 1 /proc/stat
printf '\n@@temperature\n'; cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null
printf '\n@@disk\n'; df -Pk / /userdata 2>/dev/null
printf '\n@@network\n'; ip -o -4 addr show 2>/dev/null
printf '\n@@services\n'; test -x /userdata/bin/status_check_monitor && echo monitor
printf '\n@@end\n'
'''


def parse_status(raw):
    raw = raw.replace('\r\n', '\n')
    sections = {}
    for match in re.finditer(r'@@(\w+)\n(.*?)(?=\n@@|\Z)', raw, re.S):
        sections[match[1]] = match[2].strip()
    mem = {m[1]: int(m[2]) for m in re.finditer(r'^(\w+):\s+(\d+)', sections.get('memory', ''), re.M)}
    total = mem.get('MemTotal', 0)
    available = mem.get('MemAvailable', mem.get('MemFree', 0) + mem.get('Buffers', 0) + mem.get('Cached', 0))
    cpu = sections.get('cpu', '').split()[1:9]
    cpu = [int(v) for v in cpu if v.isdigit()]
    ident = sections.get('identity', '').splitlines()
    disks = []
    seen = set()
    for line in sections.get('disk', '').splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 6 and parts[1].isdigit() and parts[-1] not in seen:
            seen.add(parts[-1])
            disks.append({'mount': parts[-1], 'total': int(parts[1])*1024, 'used': int(parts[2])*1024, 'percent': parts[4]})
    temp = sections.get('temperature', '')
    os_match = re.search(r'^PRETTY_NAME=["\']?(.*?)["\']?$', sections.get('os', ''), re.M)
    return {'kernel': ident[0] if ident else '未知', 'hostname': ident[1] if len(ident)>1 else 'TaishanPi',
            'os': os_match[1] if os_match else 'Linux', 'uptime': float(sections.get('uptime', '0').split()[0]),
            'memoryTotal': total*1024, 'memoryUsed': max(0, total-available)*1024,
            'cpuTotal': sum(cpu), 'cpuIdle': (cpu[3]+cpu[4]) if len(cpu)>4 else 0,
            'load': sections.get('load', '').split()[:3], 'temperature': float(temp)/1000 if temp.lstrip('-').isdigit() else None,
            'disks': disks, 'network': sections.get('network', ''), 'monitorAvailable': 'monitor' in sections.get('services', '')}


class App:
    def __init__(self, adb):
        self.adb = adb
        self.token = secrets.token_urlsafe(32)

    def device(self, data):
        serial = data.get('serial', '')
        lock = self.adb.lock(serial)
        if not lock.acquire(blocking=False):
            raise UserError('该设备正在执行其他操作，请稍后重试。')
        return serial, lock

    def dispatch(self, path, data):
        if path == '/api/devices':
            return {'devices': self.adb.devices(), 'adb': self.adb.path}
        if path == '/api/connect':
            addr = endpoint(data.get('address', ''))
            out, err, code = self.adb.run(['connect', addr], timeout=10, check=False)
            msg = (out.decode('utf-8', 'replace')+err).strip()
            devices = self.adb.devices()
            if not any(d['serial']==addr and d['state']=='device' for d in devices):
                raise UserError(msg or '无法连接设备。')
            return {'message': msg, 'devices': devices}
        serial, lock = self.device(data)
        try:
            if path == '/api/monitor-install':
                from monitor_plugin import install
                return install(self.adb, serial, data)
            if path in ('/api/wifi-scan', '/api/wifi-status', '/api/wifi-connect'):
                from wifi import Wifi
                wifi = Wifi(self.adb, serial, data.get('interface', ''))
                if path == '/api/wifi-scan': return wifi.scan()
                if path == '/api/wifi-status': return wifi.status()
                return wifi.connect(data)
            if path == '/api/status':
                raw, _, _ = self.adb.shell(serial, STATUS_SCRIPT)
                return parse_status(raw.decode('utf-8', 'replace'))
            if path == '/api/files':
                directory = remote_path(data.get('path', '/userdata'))
                script = 'cd '+shlex.quote(directory)+r''' || exit 1
count=0
for f in .[!.]* ..?* *; do
  [ -e "$f" ] || [ -L "$f" ] || continue
  count=$((count+1)); [ "$count" -le 1000 ] || break
  kind=f; [ ! -d "$f" ] || kind=d; [ ! -L "$f" ] || kind=l
  size=$(stat -c %s "./$f" 2>/dev/null) || size=0
  stamp=$(stat -c %Y "./$f" 2>/dev/null) || stamp=0
  printf '%s\000%s\000%s\000%s\000' "$kind" "$size" "$stamp" "$f"
done
'''
                raw, _, _ = self.adb.shell(serial, script, timeout=25)
                fields = raw.decode('utf-8', 'replace').split('\0')
                files = []
                for n in range(0, len(fields)-3, 4):
                    kind, size, stamp, name = fields[n:n+4]
                    files.append({'type': kind, 'size': int(size or 0), 'modified': int(stamp or 0), 'name': name})
                files.sort(key=lambda f:(f['type']!='d', f['name'].lower()))
                return {'files': files, 'path': directory, 'limited': len(files)>=1000}
            if path == '/api/logs':
                name = data.get('kind', 'kernel')
                if name not in LOG_PATHS:
                    raise UserError('未知日志类型。')
                script = 'dmesg | tail -n 250' if name=='kernel' else 'tail -n 250 '+shlex.quote(LOG_PATHS[name])
                out, _, _ = self.adb.shell(serial, script)
                return {'output': out.decode('utf-8', 'replace')}
            if path == '/api/command':
                if data.get('confirm') is not True:
                    raise UserError('请先确认允许在设备上执行命令。')
                command = data.get('command', '')
                if not isinstance(command, str) or not command.strip() or len(command)>8000 or '\0' in command:
                    raise UserError('请输入有效命令（最多 8000 字符）。')
                out, err, code = self.adb.shell(serial, command, timeout=30, check=False)
                return {'output': out.decode('utf-8', 'replace')+err, 'code': code}
            if path == '/api/service':
                action = data.get('action')
                if action not in SERVICE_CMDS:
                    raise UserError('未知服务操作。')
                if action!='status' and data.get('confirm') is not True:
                    raise UserError('请确认服务操作。')
                _, _, exists = self.adb.shell(serial, 'test -x '+shlex.quote(SERVICE_CMDS[action]), check=False)
                if exists: raise UserError('当前固件未安装网络健康监控脚本，暂时无法使用此项服务功能。')
                out, _, _ = self.adb.shell(serial, shlex.quote(SERVICE_CMDS[action]), timeout=20)
                return {'output': out.decode('utf-8', 'replace')}
            if path == '/api/reboot':
                if data.get('confirm') is not True:
                    raise UserError('请确认重启。')
                self.adb.run(['-s', serial, 'reboot'])
                return {'message': '已发送重启指令，请稍后刷新设备。'}
            raise UserError('不支持的操作。')
        finally:
            lock.release()
