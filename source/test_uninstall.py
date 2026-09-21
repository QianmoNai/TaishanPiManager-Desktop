import unittest
from unittest.mock import Mock,patch
from adb_core import UserError
from plugin_uninstall import uninstall,uninstall_script
import test_desktop


class UninstallBackendTests(unittest.TestCase):
    def test_confirmation_required(self):
        adb=Mock()
        with self.assertRaises(UserError): uninstall(adb,'usb',{})
        adb.shell.assert_not_called()

    def test_success_requires_marker(self):
        adb=Mock(); adb.shell.return_value=(b'UNINSTALL_OK\r\n','',0)
        self.assertEqual(uninstall(adb,'usb',{'confirm':True})['state'],'missing')
        adb.shell.return_value=(b'incomplete','',0)
        with self.assertRaises(UserError): uninstall(adb,'usb',{'confirm':True})

    def test_no_recursive_or_data_removal(self):
        script=uninstall_script('/userdata/monitor-backups/uninstall-test')
        self.assertNotIn('\x00',script); self.assertNotIn('rm -rf',script)
        self.assertNotIn('rm -f /userdata/traffic-monitor',script)
        self.assertIn('backed_up=1',script); self.assertIn('owned_process',script)


class UninstallUiTests(unittest.TestCase):
    setUpClass=classmethod(test_desktop.DesktopTests.setUpClass.__func__)
    setUp=test_desktop.DesktopTests.setUp
    tearDown=test_desktop.DesktopTests.tearDown
    wait_idle=test_desktop.DesktopTests.wait_idle
    connect_fake=test_desktop.DesktopTests.connect_fake

    def test_cancel_does_not_dispatch(self):
        self.connect_fake()
        with patch.object(self.window,'ask',return_value=False),patch.object(self.api,'dispatch') as call:
            self.window.uninstall_monitor(); call.assert_not_called()

    def test_uninstall_result_resets_catalog_and_counters(self):
        self.connect_fake(); self.window.traffic.values[0].setText('1.0 MiB/s')
        with patch.object(self.window,'ask',return_value=True),patch.object(self.api,'dispatch',return_value={'state':'missing','output':'uninstalled'}) as call:
            self.window.uninstall_monitor(); self.assertFalse(self.window.uninstall_monitor_btn.isEnabled()); self.wait_idle()
            self.assertEqual(call.call_args.args[0],'/api/monitor-uninstall')
        self.assertEqual(self.window.plugin_center.state,'missing')
        self.assertEqual(self.window.traffic.values[0].text(),'—')
        self.assertEqual(self.window.plugin_primary.text(),'安装插件')
        self.assertTrue(self.window.uninstall_monitor_btn.isHidden())

    def test_speed_test_blocks_uninstall(self):
        self.connect_fake(); self.window.traffic.speed_busy=True
        with patch.object(self.api,'dispatch') as call:
            self.window.uninstall_monitor();call.assert_not_called()
        self.assertIn('测速',self.window.banner.text());self.window.traffic.speed_busy=False
