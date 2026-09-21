import tempfile
from pathlib import Path
import unittest

from adb_core import UserError
from wifi_profiles import ProfileStore, sync_profiles


class ProfileTests(unittest.TestCase):
    def test_encrypted_roundtrip_update_delete_and_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ProfileStore(Path(folder) / 'wifi.dpapi')
            row = {'ssid_hex': '54657374', 'security': 'psk', 'password': 'test-secret-123'}
            store.save(row)
            self.assertNotIn(row['password'].encode(), store.path.read_bytes())
            self.assertEqual(store.read(), [row])
            self.assertNotIn('password', store.summaries()[0])
            row['password'] = 'updated-password'
            store.save(row)
            self.assertEqual(store.read(), [row])
            store.save({'ssid_hex': '4f74686572', 'security': 'open'})
            store.delete(row['ssid_hex'])
            self.assertEqual(len(store.read()), 1)
            store.path.write_bytes(b'broken')
            with self.assertRaises(UserError): store.read()
            self.assertEqual(store.path.read_bytes(), b'broken')

    def test_sync_updates_matching_profile_and_preserves_others(self):
        class Adb:
            def __init__(self): self.scripts = []
            def shell_input(self, serial, script, timeout):
                self.scripts.append(script)
                if len(self.scripts) == 1:
                    return b'@@interface\nwlan0\n@@profiles\nnetwork id / ssid / bssid / flags\n7\tTest\tany\t\n8\tOther\tany\t\n', '', 0
                return b'@@synced\nyes\n', '', 0
        adb = Adb()
        sync_profiles(adb, 'usb', 'wlan0', [{'ssid_hex': '54657374', 'security': 'psk', 'password': 'test-secret-123'}, {'ssid_hex': '4e6577', 'security': 'open'}])
        script = adb.scripts[-1]
        self.assertIn('id=7\n', script)
        self.assertEqual(script.count('cli add_network'), 1)
        self.assertNotIn('remove_network', script)
        self.assertNotIn('test-secret-123', script)
        self.assertNotIn('{shlex.quote(', script)
        self.assertIn('save_config', script)
        self.assertIn('/userdata/etc/tspi-wifi.conf', script)


if __name__ == '__main__': unittest.main()
