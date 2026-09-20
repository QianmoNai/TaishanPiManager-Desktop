import unittest
from unittest.mock import patch
from adb_core import UserError
import monitor_plugin
import test_desktop


class FakeAdb:
    def __init__(self,fail=''): self.scripts=[]; self.pushes=[]; self.fail=fail
    def shell(self,serial,script,**kw):
        self.scripts.append(script)
        if self.fail and self.fail in script: raise UserError('simulated failure')
        return b'INSTALL_OK\nSTOPPED\n','',0
    def run(self,args,**kw): self.pushes.append(args); return b'','',0


class MonitorInstallerTests(unittest.TestCase):
    def test_confirmation_required(self):
        adb=FakeAdb()
        with self.assertRaises(UserError): monitor_plugin.install(adb,'usb',{})
        self.assertFalse(adb.scripts)

    def test_validation_failure_does_not_install_and_cleans_staging(self):
        adb=FakeAdb(fail='test "$(sha256sum')
        with self.assertRaises(UserError): monitor_plugin.install(adb,'usb',{'confirm':True})
        self.assertFalse(any('trap rollback EXIT' in s for s in adb.scripts))
        self.assertIn('rmdir /var/run/tspi-monitor-control.lock',adb.scripts[-1])

    def test_install_has_backup_rollback_and_optional_boot_script(self):
        for autostart in (False,True):
            adb=FakeAdb(); monitor_plugin.install(adb,'usb',{'confirm':True,'autostart':autostart})
            transaction=next(s for s in adb.scripts if 'trap rollback EXIT' in s)
            self.assertIn('.absent',transaction); self.assertIn('committed=1',transaction)
            self.assertEqual('/etc/init.d/S95check-monitor' in transaction,autostart)
            self.assertNotIn('rm -rf',transaction)


class MonitorUiTests(unittest.TestCase):
    setUpClass=classmethod(test_desktop.DesktopTests.setUpClass.__func__)
    setUp=test_desktop.DesktopTests.setUp
    tearDown=test_desktop.DesktopTests.tearDown
    wait_idle=test_desktop.DesktopTests.wait_idle
    connect_fake=test_desktop.DesktopTests.connect_fake
    def test_cancel_installs_nothing(self):
        self.connect_fake()
        with patch.object(self.window,'ask',return_value=False),patch.object(self.api,'dispatch') as call:
            self.window.install_monitor(); call.assert_not_called()
    def test_install_options_and_result(self):
        self.connect_fake(); self.window.go(4); self.window.monitor_autostart.setChecked(True)
        with patch.object(self.window,'ask',return_value=True),patch.object(self.api,'dispatch',return_value={'output':'installed'}) as call:
            self.window.install_monitor(); self.assertFalse(self.window.install_monitor_btn.isEnabled()); self.wait_idle()
            self.assertTrue(call.call_args.args[1]['autostart'])
            self.assertEqual(call.call_args.args[0],'/api/monitor-install')
        self.assertIn('安装成功',self.window.banner.text())
