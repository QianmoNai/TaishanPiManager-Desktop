"""Versioned, data-only plugin packages. Import never executes package code."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

MAX_PACKAGE = 2 * 1024 * 1024


def read_package(path):
    path = Path(path)
    if path.stat().st_size > MAX_PACKAGE:
        raise ValueError('插件包不能超过 2 MiB。')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 32 or sum(e.file_size for e in entries) > MAX_PACKAGE:
            raise ValueError('插件文件数量或解压大小超过限制。')
        names = set()
        for entry in entries:
            name = entry.filename.rstrip('/') if entry.is_dir() else entry.filename
            if (name in names or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', name)
                    or any(p in ('.', '..') for p in name.split('/'))
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('插件包包含重复、链接或不安全的路径。')
            names.add(name)
        manifest = json.loads(archive.read('plugin.json').decode('utf-8'))
        if not isinstance(manifest, dict) or manifest.get('schema_version') != 1:
            raise ValueError('不支持的插件格式版本。')
        for key, limit in [('id', 80), ('name', 80), ('version', 40), ('author', 100), ('description', 1000)]:
            value = manifest.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError('插件字段无效：' + key)
        if not re.fullmatch(r'[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+', manifest['id']):
            raise ValueError('插件 ID 必须为类似 org.example.tool 的小写标识。')
        actions = manifest.get('actions')
        if not isinstance(actions, list) or not 1 <= len(actions) <= 12:
            raise ValueError('必须提供 1–12 个操作。')
        scripts = {}
        for action in actions:
            if not isinstance(action, dict) or not isinstance(action.get('title'), str) or not 1 <= len(action['title']) <= 80:
                raise ValueError('操作标题无效。')
            script = action.get('script')
            if not isinstance(script, str) or not re.fullmatch(r'scripts/[a-zA-Z0-9_-]+\.sh', script):
                raise ValueError('操作脚本必须位于 scripts/ 下。')
            raw = archive.read(script)
            if len(raw) > 65536 or b'\0' in raw:
                raise ValueError('脚本过大或包含空字节。')
            scripts[script] = raw.decode('utf-8').replace('\r\n', '\n')
        files = {e.filename for e in entries if not e.is_dir()}
        if files != {'plugin.json', *scripts}:
            raise ValueError('第一版仅支持 plugin.json 和操作 Shell 脚本。')
    return manifest, scripts


class PluginStore:
    def __init__(self, root):
        self.root = Path(root)

    def packages(self):
        result = []
        for path in sorted(self.root.glob('*.zip')):
            try:
                manifest, _ = read_package(path)
                result.append((path, manifest))
            except Exception:
                continue
        return result

    def install(self, source):
        # Validate precisely the bytes written, not a second read of the source.
        with Path(source).open('rb') as stream:
            raw = stream.read(MAX_PACKAGE + 1)
        if len(raw) > MAX_PACKAGE:
            raise ValueError('插件包不能超过 2 MiB。')
        self.root.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, suffix='.tmp', delete=False) as f:
                temp = Path(f.name)
                f.write(raw)
            manifest, _ = read_package(temp)
            destination = self.root / (manifest['id'] + '.zip')
            if destination.exists():
                raise ValueError('同 ID 插件已存在，请先移除旧包再导入新版。')
            os.replace(temp, destination)
            return manifest
        finally:
            if temp and temp.exists():
                temp.unlink()

    def remove(self, path):
        path = Path(path)
        if path.resolve().parent != self.root.resolve() or path.suffix != '.zip':
            raise ValueError('无效插件路径。')
        # Archive removed packages so local removal is recoverable.
        archive = self.root / 'removed'
        archive.mkdir(exist_ok=True)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        os.replace(path, archive / (path.stem + '-' + digest + '.zip'))
