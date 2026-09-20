import json
import unittest
from unittest.mock import Mock,patch
from adb_core import UserError
from public_speed import public_speed_test,connectivity_test,parse_result,NODES
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
    def test_connectivity_returns_all_nodes_without_transfers(self):
        adb=FakeAdb(); result=connectivity_test(adb,'usb')
        self.assertEqual(len(result['nodes']),len(NODES))
        self.assertEqual(result['nodes'][0]['latency_ms'],10)
        self.assertFalse(result['nodes'][-1]['reachable'])
        self.assertIsNone(result['nodes'][-1]['latency_ms'])
        self.assertFalse(any(' download ' in c or ' upload ' in c for c in adb.commands))
        self.assertTrue(adb.commands[-1].startswith('rm -f'))

    def test_connectivity_all_unreachable_is_valid_result(self):
        result=connectivity_test(FakeAdb(nodes=[]),'usb')
        self.assertTrue(all(not n['reachable'] for n in result['nodes']))
    def test_probe_failure_reports_board_diagnostic(self):
        class FailingProbeAdb(FakeAdb):
            def shell(self,serial,cmd,**kw):
                self.commands.append(cmd)
                if 'ip -o' in cmd: return b'3: wlan0 inet 192.168.1.148/24','',0
                if ' probe ' in cmd: return b'DNS failed\n','',1
                return b'','',0
        with self.assertRaisesRegex(UserError,'DNS failed'):
            public_speed_test(FailingProbeAdb(),'usb','wlan0')

    def test_node_list_includes_domestic_and_overseas_entries(self):
        self.assertGreaterEqual(len(NODES),9)
        self.assertTrue(any('Leaseweb' in name for name,_,_ in NODES))
        self.assertTrue(any(host.endswith('.cn') for _,host,_ in NODES))
        self.assertFalse(any('shunicom' in host for _,host,_ in NODES))

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
    def test_connectivity_table_and_reset(self):
        from PySide6.QtTest import QTest
        self.connect_fake(); panel=self.window.traffic; panel.timer.stop()
        result=connectivity_test(FakeAdb(),'usb')
        with patch('traffic_widget.connectivity_test',return_value=result):
            panel.test_connectivity()
            for _ in range(200):
                QTest.qWait(10)
                if not panel.connectivity_busy: break
        self.assertFalse(panel.connectivity_busy)
        self.assertEqual(panel.connectivity_table.item(0,3).text(),'10.0 ms')
        self.assertEqual(panel.connectivity_table.item(8,3).text(),'—')
        self.assertIn('2/9',panel.connectivity_hint.text())
        panel.reset()
        self.assertEqual(panel.connectivity_table.item(0,3).text(),'—')

    setUpClass=classmethod(test_desktop.DesktopTests.setUpClass.__func__)
    setUp=test_desktop.DesktopTests.setUp
    tearDown=test_desktop.DesktopTests.tearDown
    wait_idle=test_desktop.DesktopTests.wait_idle
    connect_fake=test_desktop.DesktopTests.connect_fake
    def test_public_mode_hides_custom_address_and_can_run_without_monitor(self):
        from PySide6.QtTest import QTest
        self.connect_fake();panel=self.window.traffic;panel.timer.stop();panel.mode.setCurrentIndex(2)
        self.assertTrue(panel.host.isHidden());self.assertEqual('一键公网测速',panel.mode.currentText())
        result={'node':'苏州 · JSQY','target':'speedtest.jsqiuying.com:8080','interface':'wlan0','latency_ms':20,'download':GOOD,'upload':GOOD}
        with patch.object(self.window,'ask',return_value=True),patch('traffic_widget.public_speed_test',return_value=result) as run:
            panel.test_speed()
            for _ in range(200):
                QTest.qWait(10)
                if not panel.speed_busy:break
            self.assertFalse(panel.speed_busy);self.assertEqual(run.call_args.args[2],'')
        self.assertIn('苏州',panel.result.text());self.assertIn('8.00 Mbps',panel.result.text())
