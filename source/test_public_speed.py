import json
import unittest
from unittest.mock import Mock,patch
from adb_core import UserError
from public_speed import public_speed_test,parse_result,NODES
import test_desktop

GOOD={'mbps':8,'bytes':1000000,'seconds':1}
class FakeAdb:
    def __init__(self,nodes=None,fail_first=False,ip=True):
        self.commands=[];self.fail_first=fail_first;self.ip=ip
        self.nodes=[{'index':0,'latency_ms':10},{'index':1,'latency_ms':20}] if nodes is None else nodes
    def run(self,*a,**kw): return b'','',0
    def shell(self,serial,cmd,**kw):
        self.commands.append(cmd)
        if 'ip -o' in cmd: return (b'3: wlan0 inet 192.168.1.148/24' if self.ip else b''),'',0
        if ' probe ' in cmd:return json.dumps(self.nodes).encode(),'',0
        if ' download ' in cmd or ' upload ' in cmd:
            if self.fail_first and cmd.endswith(' 0'):return b'{"error":"busy"}','',1
            return json.dumps(GOOD).encode(),'',0
        return b'','',0

class PublicSpeedTests(unittest.TestCase):
    def test_auto_interface_and_domestic_result(self):
        adb=FakeAdb(); messages=[];r=public_speed_test(adb,'usb','',messages.append)
        self.assertEqual(r['interface'],'wlan0');self.assertEqual(r['node'],NODES[0][0]);self.assertEqual(r['mode'],'public')
        self.assertTrue(adb.commands[-1].startswith('rm -f /tmp/tspi-public-speed-'))
        self.assertTrue(any('上传' in m for m in messages))
    def test_fallback_never_mixes_results(self):
        r=public_speed_test(FakeAdb(fail_first=True),'usb','wlan0')
        self.assertEqual(r['node'],NODES[1][0])
    def test_all_unreachable_reports_failure_and_cleans(self):
        adb=FakeAdb(nodes=[])
        with self.assertRaisesRegex(UserError,'均不可达'):public_speed_test(adb,'usb','wlan0')
        self.assertTrue(adb.commands[-1].startswith('rm -f'))
        self.assertFalse(any(' download ' in c for c in adb.commands))
    def test_no_ip_and_invalid_interface(self):
        with self.assertRaisesRegex(UserError,'IPv4'):public_speed_test(FakeAdb(ip=False),'usb','wlan0')
        adb=Mock()
        with self.assertRaises(UserError):public_speed_test(adb,'usb','wlan0;reboot')
        adb.shell.assert_not_called()
    def test_invalid_samples_are_not_success(self):
        for sample in ({'error':'timeout'},dict(GOOD,bytes=0),dict(GOOD,mbps=float('nan')),dict(GOOD,seconds=99),{},[]):
            with self.assertRaises(UserError):parse_result(json.dumps(sample).encode())

class PublicUiTests(unittest.TestCase):
    setUpClass=classmethod(test_desktop.DesktopTests.setUpClass.__func__)
    setUp=test_desktop.DesktopTests.setUp
    tearDown=test_desktop.DesktopTests.tearDown
    wait_idle=test_desktop.DesktopTests.wait_idle
    connect_fake=test_desktop.DesktopTests.connect_fake
    def test_public_mode_hides_custom_address_and_can_run_without_monitor(self):
        from PySide6.QtTest import QTest
        self.connect_fake();panel=self.window.traffic;panel.timer.stop();panel.mode.setCurrentIndex(2)
        self.assertTrue(panel.host.isHidden());self.assertIn('国内',panel.mode.currentText())
        result={'node':'上海 · 中国联通','target':'mobile.shunicomtest.com:8080','interface':'wlan0','latency_ms':20,'download':GOOD,'upload':GOOD}
        with patch.object(self.window,'ask',return_value=True),patch('traffic_widget.public_speed_test',return_value=result) as run:
            panel.test_speed()
            for _ in range(200):
                QTest.qWait(10)
                if not panel.speed_busy:break
            self.assertFalse(panel.speed_busy);self.assertEqual(run.call_args.args[2],'')
        self.assertIn('上海',panel.result.text());self.assertIn('8.00 Mbps',panel.result.text())
