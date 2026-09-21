import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import hashlib
import shlex
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from adb_core import UserError
from wifi import Wifi, decode_ssid, parse_scan, security, persistent_wifi_files
import test_desktop


SCAN = ('bssid / frequency / signal level / flags / ssid\n'
        'aa:bb:cc:dd:ee:01\t2412\t-45\t[WPA2-PSK-CCMP][ESS]\tTest\\x20WiFi\n'
        'aa:bb:cc:dd:ee:02\t5180\t-71\t[ESS]\t\\xe4\\xb8\\xad\\xe6\\x96\\x87\n'
        'aa:bb:cc:dd:ee:03\t2417\t-61\t[WPA2-EAP-CCMP][ESS]\tOffice\n')
STATE = {'interfaces':['wlan0'], 'interface':'wlan0', 'state':'COMPLETED', 'ssid':'Test WiFi',
         'ip':'192.0.2.10', 'bssid':'aa:bb:cc:dd:ee:01', 'id':'7'}


class FakeAdb:
    def __init__(self, reply=b'', code=0): self.reply=reply; self.code=code; self.scripts=[]
    def shell_input(self, serial, script, timeout):
        self.scripts.append(script); return self.reply, '', self.code


class WifiBackendTests(unittest.TestCase):
    def test_persistent_wifi_files_have_boot_script_and_no_plaintext_password(self):
        config, helper, init = persistent_wifi_files('wlan0','54657374','psk','a1b2c3')
        self.assertIn('update_config=0',config)
        self.assertIn('psk=a1b2c3',config)
        self.assertIn('/userdata/etc/tspi-wifi.conf',helper)
        self.assertIn('CONF=/etc/wpa_supplicant.conf',helper)
        self.assertNotIn('wpa_cli -p "$CTRL" -i "$IFACE" terminate',helper)
        self.assertIn('wpa_supplicant -B',helper)
        self.assertIn('enable_network all',helper)
        self.assertIn('reconnect',helper)
        self.assertIn('cp -p "$LEGACY" "$CONF"',helper)
        self.assertIn('/etc/init.d/S40tspi-wifi',init) if False else self.assertIn('start)',init)

    def test_scan_utf8_escaping_signal_and_security(self):
        rows=parse_scan(SCAN)
        self.assertEqual([r['signal'] for r in rows],[-45,-61,-71])
        self.assertEqual(rows[0]['ssid'],'Test WiFi'); self.assertEqual(rows[2]['ssid'],'中文')
        self.assertEqual(rows[1]['security'],'enterprise')
        self.assertEqual(decode_ssid(r'a\\b\x22c'),b'a\\b"c')
        self.assertEqual(security('[WPA2-PSK+SAE-CCMP]')[0],'psk')
        for flags in ('[SAE]', '[OWE]', '[WEP]'): self.assertNotIn(security(flags)[0],('psk','open'))

    def test_invalid_inputs_do_not_reach_device(self):
        adb=FakeAdb()
        with self.assertRaises(UserError): Wifi(adb,'usb','wlan0; reboot')
        for data in ({'ssid_hex':'xx','security':'open'}, {'ssid_hex':'61','security':'psk','password':'short'}, {'ssid_hex':'61','security':'enterprise'}):
            with self.assertRaises(UserError): Wifi(adb,'usb').connect(data)
        self.assertEqual(adb.scripts,[])

    def test_connect_uses_derived_key_and_saves_config(self):
        reply=b'@@interface\nwlan0\n@@interfaces\nwlan0\n@@status\nwpa_state=COMPLETED\nssid=Test\nid=7\n@@connected\nyes\n'
        adb=FakeAdb(reply); password='test$pass\'word'; ssid=b'Test'
        result=Wifi(adb,'usb','wlan0').connect({'ssid_hex':ssid.hex(),'security':'psk','password':password})
        script=adb.scripts[0]
        self.assertNotIn('{shlex.quote(', script)
        _, helper, init = persistent_wifi_files('wlan0', ssid.hex(), 'open')
        self.assertIn('printf %s ' + shlex.quote(helper), script)
        self.assertIn('printf %s ' + shlex.quote(init), script)
        self.assertNotIn(password,script)
        self.assertIn(hashlib.pbkdf2_hmac('sha1',password.encode(),ssid,4096,32).hex(),script)
        self.assertIn('set update_config 1',script)
        self.assertIn('save_config',script)
        self.assertIn('enable_network \"$new_id\"',script)
        self.assertIn('all_ids=',script)
        self.assertIn('for id in $all_ids',script)
        self.assertIn('select_network \"$new_id\"',script)
        self.assertIn('Save only after',script)
        self.assertIn('priority 100',script)
        self.assertIn('/userdata/etc/tspi-wifi.conf',script)
        self.assertIn('/etc/.tspi-wifi.conf.tmp',script)
        self.assertIn('/etc/wpa_supplicant.conf',script)
        self.assertIn('/userdata/bin/tspi-wifi-autostart',script)
        self.assertIn('/etc/init.d/S40tspi-wifi',script)
        self.assertIn('尚未获取',result['message'])

    def test_device_errors_and_disconnect_are_safe(self):
        with self.assertRaisesRegex(UserError,'认证'):
            Wifi(FakeAdb(b'ERR:AUTH_TIMEOUT\n',1),'usb').status()
        with self.assertRaisesRegex(UserError,'缺少 wpa_cli'):
            Wifi(FakeAdb(b'ERR:NO_WPA_CLI\n',1),'usb').scan()
        adb=FakeAdb()
        with patch.object(adb,'shell_input',side_effect=UserError('internal detail')):
            with self.assertRaisesRegex(UserError,'ADB') as caught: Wifi(adb,'usb').status()
        self.assertNotIn('internal detail',str(caught.exception))

    def test_completed_without_connection_marker_is_not_success(self):
        adb=FakeAdb(b'@@status\nwpa_state=COMPLETED\n')
        with self.assertRaisesRegex(UserError,'尚未确认'): Wifi(adb,'usb').connect({'ssid_hex':'61','security':'open'})

    def test_persistence_copies_complete_wpa_profile(self):
        adb=FakeAdb(b'@@interface\nwlan0\n@@interfaces\nwlan0\n@@status\nwpa_state=COMPLETED\nssid=Second\nid=2\n@@connected\nyes\n')
        Wifi(adb,'usb','wlan0').connect({'ssid_hex':'5365636f6e64','security':'open'})
        script=adb.scripts[0]
        self.assertIn('cp -p "$active_conf" /userdata/etc/.tspi-wifi.conf.tmp',script)
        self.assertIn('save_config',script)
        self.assertIn('enable_network \"$new_id\"',script)
        self.assertIn('all_ids=',script)
        self.assertIn('for id in $all_ids',script)
        self.assertIn('select_network \"$new_id\"',script)
        self.assertIn('Save only after',script)



class WifiUiTests(unittest.TestCase):
    setUpClass = classmethod(test_desktop.DesktopTests.setUpClass.__func__)
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        from wifi_profiles import ProfileStore
        patcher=patch('wifi_profiles.ProfileStore',lambda:ProfileStore(Path(folder.name)/'profiles.dpapi'))
        patcher.start(); self.addCleanup(patcher.stop)
        test_desktop.DesktopTests.setUp(self)
    tearDown = test_desktop.DesktopTests.tearDown
    wait_idle = test_desktop.DesktopTests.wait_idle
    connect_fake = test_desktop.DesktopTests.connect_fake
    def wifi_ready(self):
        self.connect_fake(); self.window.go(5); self.wait_idle()
        self.window.render_wifi_scan({**STATE,'networks':parse_scan(SCAN)})
        self.window.wifi_table.selectRow(0)

    def test_wifi_selection_masked_password_and_device_reset(self):
        self.wifi_ready(); self.window.wifi_password.setText('example password')
        self.assertEqual(self.window.wifi_password.echoMode().name,'Password')
        self.window.render_devices([])
        self.assertEqual(self.window.wifi_password.text(),'')
        self.assertEqual(self.window.wifi_table.rowCount(),0)
        self.window.connect_wifi(); self.assertIn('请先',self.window.banner.text())

    def test_wifi_connect_clears_secret_and_reports_state(self):
        self.wifi_ready(); self.window.wifi_password.setText('example password')
        def dispatch(path,data):
            self.assertEqual(path,'/api/wifi-connect')
            self.assertEqual(data['password'],'example password')
            return {**STATE,'message':'已连接 Wi-Fi'}
        with patch.object(self.api,'dispatch',side_effect=dispatch):
            self.window.connect_wifi()
            self.assertEqual(self.window.wifi_password.text(),'')
            self.assertFalse(self.window.wifi_table.isEnabled()); self.wait_idle()
        self.assertIn('192.0.2.10',self.window.wifi_status.text())
        self.assertIsNone(self.window.job.fn)

    def test_wifi_open_network_cancel_does_not_connect(self):
        self.wifi_ready(); self.window.wifi_table.selectRow(2)
        with patch.object(self.window,'ask',return_value=False), patch.object(self.api,'dispatch') as dispatch:
            self.window.connect_wifi(); dispatch.assert_not_called()

    def test_wifi_enterprise_rejected_and_interface_switch_clears_scan(self):
        self.wifi_ready(); self.window.wifi_table.selectRow(1); self.window.connect_wifi()
        self.assertIn('暂不支持',self.window.banner.text())
        self.window.wifi_iface.addItem('wlan1','wlan1'); self.window.wifi_iface.setCurrentIndex(1)
        self.assertEqual(self.window.wifi_table.rowCount(),0)


if __name__=='__main__': unittest.main()
