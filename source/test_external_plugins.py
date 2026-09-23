import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from external_plugins import PluginStore, read_package


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.manifest = dict(schema_version=1, id='org.test.info', name='Info',
                             version='1.0', author='Test', description='Read only',
                             actions=[dict(title='Run', script='scripts/info.sh')])

    def package(self, extras=None):
        path = self.root / 'test.zip'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('plugin.json', json.dumps(self.manifest))
            z.writestr('scripts/info.sh', 'uname -a\n')
            for name, data in (extras or {}).items():
                z.writestr(name, data)
        return path

    def test_install_restart_remove_restore(self):
        store = PluginStore(self.root / 'installed')
        store.install(self.package())
        self.assertEqual(len(PluginStore(store.root).packages()), 1)
        with self.assertRaises(ValueError):
            store.install(self.package())
        path, manifest = store.packages()[0]
        self.assertEqual(manifest['id'], self.manifest['id'])
        store.remove(path)
        self.assertEqual(store.packages(), [])
        store.install(next((store.root / 'removed').glob('*.zip')))
        self.assertEqual(len(store.packages()), 1)

    def test_reject_paths_and_extra_code(self):
        for name in ('../escape', '/absolute', 'scripts/../../escape', 'module.py'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                read_package(self.package({name: 'bad'}))

    def test_invalid_schema_and_identity(self):
        for key, value in [('schema_version', 2), ('id', '../../escape'), ('actions', [])]:
            previous = self.manifest[key]
            self.manifest[key] = value
            with self.assertRaises(ValueError):
                read_package(self.package())
            self.manifest[key] = previous

    def test_large_archive_rejected(self):
        with self.assertRaises(ValueError):
            read_package(self.package({'huge': 'x' * (2 * 1024 * 1024)}))

    def test_invalid_stored_package_skipped(self):
        store = PluginStore(self.root)
        (self.root / 'bad.zip').write_bytes(b'not zip')
        self.assertEqual(store.packages(), [])

    def test_directory_entries_supported(self):
        path = self.package()
        with zipfile.ZipFile(path, 'a') as z:
            z.writestr('scripts/', '')
        self.assertEqual(read_package(path)[0]['id'], self.manifest['id'])

    def test_symlink_rejected(self):
        path = self.package()
        with zipfile.ZipFile(path, 'a') as z:
            info = zipfile.ZipInfo('link')
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            z.writestr(info, '/etc/passwd')
        with self.assertRaises(ValueError):
            read_package(path)
