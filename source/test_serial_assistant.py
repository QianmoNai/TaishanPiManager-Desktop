import json,struct,unittest
from unittest.mock import Mock,patch
from serial_assistant import WaveDecoder,encode_send,pin_config,prepare
from adb_core import UserError
import test_desktop

class SerialDataTests(unittest.TestCase):
    def test_firewater_partial_frames_and_malformed(self):
        decoder=WaveDecoder('firewater'); self.assertEqual(decoder.feed(b'ch:1.25,'),[])
        self.assertEqual(decoder.feed(b'2.5\r\n3,4\nnot,data\ninf,5\n'),[[1.25,2.5],[3,4]])
        self.assertEqual(decoder.dropped,2)
        decoder.feed(b'x'*10000); self.assertLessEqual(len(decoder.buffer),4096)
    def test_justfloat_binary_fragmentation(self):
        decoder=WaveDecoder('justfloat'); frame=struct.pack('<ff',1.25,-2.5)+b'\x00\x00\x80\x7f'
        results=[]
        for b in frame*2: results+=decoder.feed(bytes([b]))
        self.assertEqual(results,[[1.25,-2.5],[1.25,-2.5]])
        self.assertEqual(decoder.feed(b'bad'+b'\x00\x00\x80\x7f'),[])
    def test_send_encoding_and_limits(self):
        self.assertEqual(encode_send('00 ff 41',True),b'\0\xffA')
        self.assertEqual(encode_send('中文',False,'crlf'),'中文\r\n'.encode())
        for value in ('0','GG','0x00',''):
            with self.assertRaises(UserError): encode_send(value,True)
        with self.assertRaises(UserError): encode_send('x'*16385)
    def test_pin_export_and_reserved(self):
        text=pin_config({'index':3,'symbol':'uart3'},{'symbol':'uart3m1_xfer','pins':['GPIO3_C0','GPIO3_B7']})
        self.assertIn('pinctrl-0 = <&uart3m1_xfer>',text); self.assertIn('status = "okay"',text)
        with self.assertRaises(UserError): pin_config({'index':1,'symbol':'uart1'},{'symbol':'uart1m0_xfer'})
        with self.assertRaises(UserError): pin_config({'index':3,'symbol':'uart3'},{'symbol':'uart4m0_xfer'})
    def test_prepare_rejects_busy_before_upload(self):
        adb=Mock()
        with patch('serial_assistant.inventory',return_value={'ports':[{'path':'/dev/ttyS3','reserved':'','owners':[100]}]}),self.assertRaises(UserError): prepare(adb,'usb','/dev/ttyS3',115200,8,'none',1,'none')
        adb.run.assert_not_called()
        with self.assertRaises(UserError): prepare(adb,'usb','/dev/ttyS3;reboot',115200,8,'none',1,'none')

class SerialUiTests(unittest.TestCase):
    setUpClass=classmethod(test_desktop.DesktopTests.setUpClass.__func__)
    setUp=test_desktop.DesktopTests.setUp
    tearDown=test_desktop.DesktopTests.tearDown
    wait_idle=test_desktop.DesktopTests.wait_idle
    def test_catalog_and_protocol_and_reset(self):
        w=self.window; self.assertIn('串口助手',[c['name'] for c in w.plugin_center.cards]); w.go(4,False); w.open_serial()
        self.assertTrue(w.serial_tool.isVisible()); self.assertFalse(w.proxy.isVisible())
        p=w.serial_tool; p.protocol.setCurrentIndex(1); self.assertEqual(p.decoder.feed(b'1,2\n'),[[1,2]])
        p.plot.points.extend([[1,2]]); p.rx=20; p.receive.setPlainText('data'); p.reset()
        self.assertEqual(p.rx,0); self.assertEqual(len(p.plot.points),0); self.assertEqual(p.receive.toPlainText(),'')
    def test_no_send_until_ready_and_repeat_stops(self):
        p=self.window.serial_tool; p.input.setPlainText('test'); p.send(); self.assertIn('先打开',p.state.text())
        p.periodic.setChecked(True); self.assertFalse(p.repeat.isActive()); self.assertFalse(p.periodic.isChecked())
        p.connected=True; p.periodic.setChecked(True); self.assertTrue(p.repeat.isActive()); p.stop(); self.assertFalse(p.repeat.isActive())

if __name__=='__main__': unittest.main()
