"""Optional board transport and incremental LD06 packet decoder."""
import hashlib
import math
from pathlib import Path
import shlex
import struct
import sys
import secrets
import zipfile
from adb_core import UserError
from serial_assistant import inventory

ASSETS = (Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent) / 'plugins/ld06-radar'
TARGET = '/userdata/bin/tspi-ld06-helper.pl'

def digest(): return hashlib.sha256((ASSETS/'ld06-helper.pl').read_bytes()).hexdigest()

def status(adb, serial):
    out, _, _ = adb.shell(serial, f'if [ -f {TARGET} ]; then sha256sum {TARGET}; else echo missing; fi')
    value = out.decode().split()[0]
    return {'state': 'missing' if value == 'missing' else 'installed' if value == digest() else 'update'}

def install(adb, serial):
    temp = '/tmp/tspi-ld06-' + secrets.token_hex(12) + '.pl'
    try:
        adb.run(['-s', serial, 'push', str(ASSETS/'ld06-helper.pl'), temp])
        adb.shell(serial, f'''set -e
test "$(sha256sum {temp} | cut -d ' ' -f 1)" = {digest()}
LC_ALL=C LANG=C perl -c {temp}
mkdir -p /userdata/bin
if [ -f {TARGET} ]; then cp -p {TARGET} {TARGET}.bak; fi
cp {temp} {TARGET}.new
chmod 700 {TARGET}.new
mv {TARGET}.new {TARGET}
sync
''', timeout=25)
    finally:
        adb.shell(serial, 'rm -f '+temp, check=False)
    return status(adb, serial)

def uninstall(adb, serial):
    adb.shell(serial, f'rm -f {TARGET} {TARGET}.bak {TARGET}.new')
    return {'state': 'missing'}

def export_package(path):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(ASSETS.iterdir()):
            if file.is_file(): archive.write(file, 'ld06-radar/'+file.name)

def prepare(adb, serial):
    if status(adb, serial)['state'] != 'installed': raise UserError('请先安装或升级 LD06 插件。')
    port = next((p for p in inventory(adb, serial)['ports'] if p['path'] == '/dev/ttyS3'), None)
    if not port: raise UserError('未发现 UART3 /dev/ttyS3，请检查固件串口配置。')
    if port['reserved'] or port['owners']:
        raise UserError('UART3 已被占用。请先关闭串口助手或暂停 LD06/LCD 监控，使用结束后恢复原监控。')
    return 'LC_ALL=C LANG=C perl '+shlex.quote(TARGET)+' open /dev/ttyS3 230400 8 none 1 none'

def crc8(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8): crc = ((crc << 1) ^ (0x4d if crc & 0x80 else 0)) & 255
    return crc

class Decoder:
    def __init__(self):
        self.buffer = bytearray(); self.frames = 0; self.bad = 0; self.speed = 0
    def feed(self, raw):
        self.buffer.extend(raw); points = []
        while len(self.buffer) >= 2:
            index = self.buffer.find(b'\x54\x2c')
            if index < 0:
                self.buffer[:] = self.buffer[-1:] if self.buffer[-1:] == b'\x54' else b''
                break
            if index: del self.buffer[:index]
            if len(self.buffer) < 47: break
            frame = bytes(self.buffer[:47])
            if crc8(frame[:-1]) != frame[-1]:
                self.bad += 1; del self.buffer[0]; continue
            del self.buffer[:47]
            speed, start = struct.unpack_from('<HH', frame, 2)
            end = struct.unpack_from('<H', frame, 42)[0]
            if start >= 36000 or end >= 36000: self.bad += 1; continue
            self.frames += 1; self.speed = speed / 360
            span = (end-start) % 36000
            for i in range(12):
                distance, confidence = struct.unpack_from('<HB', frame, 6+3*i)
                if distance:
                    points.append((((start+span*i/11)/100) % 360, distance/1000, confidence))
        return points
