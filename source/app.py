"""Local-only ADB dashboard for TaishanPi. Python standard library only."""
from __future__ import annotations

import argparse
import ipaddress
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse
import webbrowser

MAX_TRANSFER = 512 * 1024 * 1024
MAX_OUTPUT = 2 * 1024 * 1024
ASSETS = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'web'
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

    def run(self, args, timeout=12, check=True):
        if not self.path:
            raise UserError('未找到 ADB。请保留软件目录中的 adb 文件夹，或设置 TAISHAN_ADB。')
        # File-backed output prevents a verbose command from exhausting RAM.
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                result = subprocess.run([self.path, *args], stdout=out, stderr=err, stdin=subprocess.DEVNULL,
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
        return self.run(['-s', serial, 'exec-out', 'sh', '-c', shlex.quote(script)], timeout, check)


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


class Handler(BaseHTTPRequestHandler):
    server_version = 'TaishanPiManager/1.0'

    def log_message(self, *args):
        pass

    @property
    def app(self):
        return self.server.app

    def send(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        self.wfile.write(body)

    def authorized(self, token=True):
        expected = f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host') != expected:
            self.send(403, {'error':'无效的本机访问地址。'}); return False
        origin = self.headers.get('Origin')
        if origin and origin != 'http://'+expected:
            self.send(403, {'error':'禁止跨站请求。'}); return False
        if token and not secrets.compare_digest(self.headers.get('X-App-Token', ''), self.app.token):
            self.send(403, {'error':'会话已失效，请刷新页面。'}); return False
        return True

    def do_GET(self):
        if not self.authorized(False): return
        path = urlparse(self.path).path
        if path == '/':
            html = (ASSETS/'index.html').read_text(encoding='utf-8').replace('__APP_TOKEN__', self.app.token)
            self.send(200, html.encode('utf-8'), 'text/html; charset=utf-8')
        elif path in ('/app.js', '/style.css'):
            self.send(200, (ASSETS/path[1:]).read_bytes(), 'text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
        else:
            self.send(404, {'error':'页面不存在'})

    def do_POST(self):
        if not self.authorized(): return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size < 0: raise UserError('无效的数据长度。')
            if urlparse(self.path).path == '/api/upload':
                return self.upload(size)
            if size > 16384: raise UserError('请求过大。')
            data = json.loads(self.rfile.read(size) or b'{}')
            if not isinstance(data, dict): raise UserError('无效请求。')
            if self.path == '/api/download': return self.download(data)
            if self.path == '/api/quit':
                self.send(200, {'message':'管理服务已退出，可关闭此页面。'})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self.send(200, self.app.dispatch(self.path, data))
        except (UserError, ValueError) as exc:
            self.send(400, {'error':str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.send(500, {'error':'操作失败，请检查设备连接和路径后重试。'})

    def upload(self, size):
        if size > MAX_TRANSFER: raise UserError('单个文件上限为 512 MB。固件刷写请使用专用烧录工具。')
        query = parse_qs(urlparse(self.path).query)
        data = {key:values[0] for key, values in query.items()}
        if data.get('confirm') != 'yes': raise UserError('上传需要确认目标路径。')
        dest = remote_path(data.get('path', ''))
        if dest == '/': raise UserError('目标必须包含文件名。')
        serial, lock = self.app.device(data)
        try:
            with tempfile.TemporaryDirectory(prefix='tspi-upload-') as tmp:
                source = Path(tmp)/'payload'
                self.connection.settimeout(60)
                with source.open('wb') as file:
                    remaining = size
                    while remaining:
                        chunk = self.rfile.read(min(1024*1024, remaining))
                        if not chunk: raise UserError('上传中断。')
                        file.write(chunk); remaining -= len(chunk)
                out, _, _ = self.app.adb.run(['-s', serial, 'push', str(source), dest], timeout=180)
                self.send(200, {'message':'上传完成', 'output':out.decode('utf-8','replace')})
        finally:
            lock.release()

    def download(self, data):
        source = remote_path(data.get('path', ''))
        serial, lock = self.app.device(data)
        try:
            raw, _, _ = self.app.adb.shell(serial, 'test -f '+shlex.quote(source)+' && stat -Lc %s '+shlex.quote(source))
            size = int(raw.strip())
            if size > MAX_TRANSFER: raise UserError('单个下载文件上限为 512 MB。')
            with tempfile.TemporaryDirectory(prefix='tspi-download-') as tmp:
                local = Path(tmp)/'payload'
                self.app.adb.run(['-s', serial, 'pull', source, str(local)], timeout=180)
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(local.stat().st_size))
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''"+quote(PurePosixPath(source).name))
                self.send_header('Cache-Control','no-store')
                self.end_headers()
                with local.open('rb') as file:
                    shutil.copyfileobj(file, self.wfile)
        finally:
            lock.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18766)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--adb')
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError:
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.app = App(Adb(args.adb))
    url = f'http://127.0.0.1:{server.server_port}'
    if not args.no_browser:
        webbrowser.open(url)
    if sys.stdout:
        print(url, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
