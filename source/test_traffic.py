import json
import unittest
from unittest.mock import Mock,patch
from adb_core import UserError
from traffic import status,parse_iperf,speed_test
from traffic_widget import amount
import test_desktop

def sample(stamp=100):
    return {'state':'running','version':2,'uptime':stamp,'started':100,'interfaces':{
        'wlan0':{'rx':2000,'tx':1000,'rx_rate':2048,'tx_rate':1024,'total_rx':4096,'total_tx':2048,'present':True},
        'eth0':{'rx':0,'tx':0,'rx_rate':0,'tx_rate':0,'total_rx':0,'total_tx':0,'present':True}}}

class TrafficBackendTests(unittest.TestCase):
    def test_fresh_stale_and_stopped(self):
        adb=Mock()
        for age,state in ((2,'running'),(12,'stale'),(-1,'stale')):
            data=sample(); data.pop('state')
            adb.shell.return_value=(json.dumps(data).encode()+f'\r\n{100+age} 1\r\n'.encode(),'',0)
            self.assertEqual(status(adb,'dev')['state'],state)
        adb.shell.return_value=(b'{"state":"stopped"}\n','',0)
        self.assertEqual(status(adb,'dev')['state'],'stopped')
    def test_speed_errors_are_not_zero_results(self):
        for raw in (b'{}',b'broken',b'{"error":"connection refused"}'):
            with self.assertRaises(UserError): parse_iperf(raw)
        raw=b'{"end":{"sum_received":{"bits_per_second":8000000,"bytes":5000000,"seconds":5}}}'
        self.assertEqual(parse_iperf(raw)['mbps'],8)
    def test_invalid_parameters_never_execute(self):
        adb=Mock()
        for interface,host,port in [('wlan0;reboot','',5201),('wlan0','x;reboot',5201),('wlan0','good',0)]:
            with self.assertRaises(UserError): speed_test(adb,'dev',interface,host,port)
        adb.shell.assert_not_called()
    def test_custom_server_upload_and_download_direction(self):
        adb=Mock(); result=b'{"end":{"sum_received":{"bits_per_second":1000000,"seconds":5}}}'
        adb.shell.side_effect=[(b'3: wlan0 inet 192.168.1.148/24','',0),(result,'',0),(result,'',0)]
        result=speed_test(adb,'dev','wlan0','test.example',5201)
        self.assertEqual(result['mode'],'server')
        self.assertNotIn(' -R',adb.shell.call_args_list[1].args[1])
        self.assertIn(' -R',adb.shell.call_args_list[2].args[1])
    def test_units(self):
        self.assertEqual(amount(1024),'1.0 KiB')

class TrafficUiTests(unittest.TestCase):
    setUpClass=classmethod(test_desktop.DesktopTests.setUpClass.__func__)
    setUp=test_desktop.DesktopTests.setUp
    tearDown=test_desktop.DesktopTests.tearDown
    wait_idle=test_desktop.DesktopTests.wait_idle
    connect_fake=test_desktop.DesktopTests.connect_fake
    def test_counters_interface_change_and_stale(self):
        panel=self.window.traffic; panel.timer.stop(); panel.render(sample())
        self.assertEqual(panel.iface.currentText(),'wlan0')
        self.assertEqual(panel.values[0].text(),'2.0 KiB/s')
        self.assertEqual(panel.values[2].text(),'4.0 KiB')
        panel.render(sample(102)); self.assertEqual(len(panel.graph.points),2)
        panel.iface.setCurrentText('eth0'); self.assertEqual(len(panel.graph.points),1)
        panel.render({'state':'stale'}); self.assertEqual(panel.values[0].text(),'—')
        self.assertEqual(len(panel.graph.points),0)
    def test_speed_cancellation_does_not_execute(self):
        self.connect_fake(); panel=self.window.traffic; panel.timer.stop(); panel.render(sample())
        with patch.object(self.window,'ask',return_value=False),patch('traffic_widget.speed_test') as speed:
            panel.test_speed(); speed.assert_not_called()
    def test_device_reset_clears_traffic(self):
        self.connect_fake(); panel=self.window.traffic; panel.timer.stop(); panel.render(sample())
        self.window.render_devices([])
        self.assertEqual(panel.iface.count(),0); self.assertEqual(panel.values[2].text(),'—')

if __name__=='__main__': unittest.main()
