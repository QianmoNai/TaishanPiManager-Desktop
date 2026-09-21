"""Current-user encrypted Wi-Fi profiles; never included in portable packages."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import shlex

from adb_core import UserError
from wifi import Wifi, decode_ssid, persistence_script

_lock = threading.RLock()


def protect(data, decrypt=False):
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    src = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    dst = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        args = (ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst))
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        args = (ctypes.byref(src), 'TaishanPi Wi-Fi', None, None, None, 1, ctypes.byref(dst))
    fn.restype = wintypes.BOOL
    if not fn(*args):
        raise UserError('无法读取或加密本地 Wi-Fi 配置，请使用保存配置时的 Windows 用户。')
    try:
        return ctypes.string_at(dst.data, dst.size)
    finally:
        kernel.LocalFree(ctypes.cast(dst.data, ctypes.c_void_p))


def validate(data):
    ssid = data.get('ssid_hex', '').lower()
    mode = data.get('security')
    password = data.get('password', '')
    if not re.fullmatch(r'(?:[0-9a-f]{2}){1,32}', ssid) or mode not in ('psk', 'open'):
        raise UserError('请选择有效的 WPA/WPA2 或开放 Wi-Fi。')
    if mode == 'psk' and not (re.fullmatch('[0-9a-fA-F]{64}', password) or
            8 <= len(password.encode('utf-8')) <= 63 and all(32 <= ord(c) != 127 for c in password)):
        raise UserError('Wi-Fi 密码须为 8–63 字节或 64 位十六进制密钥。')
    return {'ssid_hex': ssid, 'security': mode, 'password': password if mode == 'psk' else ''}


class ProfileStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ['LOCALAPPDATA']) / 'TaishanPiManager' / 'wifi-profiles.dpapi'

    def read(self):
        with _lock:
            if not self.path.exists(): return []
            try:
                return [validate(row) for row in json.loads(protect(self.path.read_bytes(), True))]
            except UserError: raise
            except Exception:
                raise UserError('本地 Wi-Fi 配置读取失败，原文件已保留。') from None

    def write(self, rows):
        with _lock:
            data = protect(json.dumps(rows, ensure_ascii=False).encode('utf-8'))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_bytes(data)
            os.replace(tmp, self.path)

    def save(self, data):
        row = validate(data)
        with _lock:
            rows = [r for r in self.read() if r['ssid_hex'] != row['ssid_hex']]
            self.write(rows + [row])

    def delete(self, ssid):
        with _lock: self.write([r for r in self.read() if r['ssid_hex'] != ssid])

    def summaries(self):
        return [{k: v for k, v in row.items() if k != 'password'} for row in self.read()]


def sync_profiles(adb, serial, interface, rows):
    rows = [validate(r) for r in rows]
    if not rows: raise UserError('请先保存本地 Wi-Fi 配置。')
    wifi = Wifi(adb, serial, interface)
    # Keep existing profiles. Match exact SSID bytes instead of shell text.
    current = wifi.execute(wifi.prepare(True) + '\nprintf "@@profiles\\n"; cli list_networks\n')
    existing = {}
    for line in current['raw'].get('profiles', '').splitlines():
        parts = line.split('\t')
        if len(parts) >= 2 and parts[0].isdigit():
            existing.setdefault(decode_ssid(parts[1]).hex(), []).append(parts[0])
    script = wifi.prepare(True) + '''
fail_setup() { echo ERR:SETUP_FAILED; exit 1; }
fail_save() { echo ERR:SAVE_FAILED; exit 1; }
'''
    for row in rows:
        ids = existing.get(row['ssid_hex'], [None])
        for ident in ids:
            script += ('id=' + ident + '\n') if ident else 'id=$(cli add_network)\n'
            script += 'case "$id" in ""|*[!0-9]*) fail_setup;; esac\n'
            fields = {'ssid': row['ssid_hex'], 'key_mgmt': 'NONE'}
            if row['security'] == 'psk':
                password = row['password']
                psk = password.lower() if re.fullmatch('[0-9a-fA-F]{64}', password) else hashlib.pbkdf2_hmac('sha1', password.encode(), bytes.fromhex(row['ssid_hex']), 4096, 32).hex()
                fields.update(key_mgmt='WPA-PSK', psk=psk)
            fields['disabled'] = '0'
            for name, value in fields.items():
                script += f'cli set_network "$id" {name} {shlex.quote(value)} | grep -q "^OK$" || fail_setup\n'
    script += '''cli set update_config 1 | grep -q '^OK$' || fail_save
cli save_config | grep -q '^OK$' || fail_save
'''
    script += persistence_script(current['interface'])
    script += '''
cli enable_network all >/dev/null
cli reconnect >/dev/null
printf '@@status\n'; cli status
printf '@@synced\nyes\n'
'''
    result = wifi.execute(script)
    if result['raw'].get('synced') != 'yes': raise UserError('未收到完整的配置同步结果，请刷新后检查。')
    return {'message': f'已同步 {len(rows)} 个本地 Wi-Fi 配置并写入开机配置，保留板端其他网络。'}
