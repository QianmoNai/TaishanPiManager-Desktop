import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import sys
import threading
import time
import unittest
import tempfile
from unittest.mock import patch

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from desktop import Window, STYLE, configure_app
from adb_core import App, Adb, UserError, remote_path
from terminal_widget import Terminal


SAMPLE={'hostname':'taishanpi','os':'Buildroot 2024.02','kernel':'Linux 6.1.141 · aarch64','uptime':96500,
    'memoryTotal':1024**3,'memoryUsed':348*1024**2,'cpuTotal':1000,'cpuIdle':800,
    'load':['0.18','0.12','0.08'],'temperature':43.6,
    'disks':[{'mount':'/','total':12*1024**3,'used':1.6*1024**3,'percent':'13%'},{'mount':'/userdata','total':7.5*1024**3,'used':1.2*1024**3,'percent':'16%'}],
    'network':'eth0   192.168.1.150/24','monitorAvailable':True}


class FakeApi:
    def __init__(self): self.adb=Adb(); self.calls=[]
    def dispatch(self,path,data):
        self.calls.append((path,data))
        if path=='/api/status': return SAMPLE.copy()
        if path=='/api/files': return {'path':data['path'],'limited':False,'files':[{'name':'log','type':'d','size':0,'modified':1700000000},{'name':"a'; test.txt",'type':'f','size':8192,'modified':1700000000}]}
        if path=='/api/logs': return {'output':'kernel ready\n测试日志'}
        if path=='/api/command': return {'output':'Linux test','code':0}
        return {'devices':[]}


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([]); configure_app(cls.app)

    def setUp(self):
        self.api=FakeApi(); self.window=Window(self.api,autostart=False); self.window.timer.stop(); self.window.show(); QTest.qWait(30)

    def tearDown(self):
        self.wait_idle(); self.window.close(); self.window.deleteLater(); QTest.qWait(20)

    def wait_idle(self):
        for _ in range(300):
            QTest.qWait(10)
            time.sleep(.001)
            if not self.window.busy and not self.window.pool.activeThreadCount(): return
        self.fail('Background operation never completed')

    def connect_fake(self):
        with patch.object(Terminal,'connect_device'):
            self.window.render_devices([{'serial':'usb-test','state':'device','transport':'USB'}]); QTest.qWait(30); self.wait_idle()

    def test_empty_device_and_all_pages(self):
        self.window.render_devices([])
        self.assertEqual(self.window.serial,'')
        for index in range(6):
            self.window.go(index); self.assertEqual(self.window.stack.currentIndex(),index)
        self.window.load_files(); self.assertIn('请先',self.window.banner.text())

    def test_status_and_disconnect_reset(self):
        self.connect_fake(); self.window.render_status(SAMPLE)
        second=SAMPLE.copy(); second.update(cpuTotal=1100,cpuIdle=870); self.window.render_status(second)
        self.assertEqual(self.window.metrics[0].value.text(),'30.0%')
        self.assertIn('34.0%',self.window.metrics[1].value.text())
        self.assertEqual(self.window.hostname.text(),'taishanpi')
        self.window.render_devices([])
        self.assertEqual(self.window.metrics[0].value.text(),'—')
        self.assertEqual(self.window.info_labels['kernel'].text(),'—')

    def test_files_logs_and_terminal_page(self):
        self.connect_fake(); self.window.go(1); self.window.load_files(); self.wait_idle()
        self.assertEqual(self.window.table.rowCount(),2)
        self.assertEqual(self.window.table.item(1,0).text(),"a'; test.txt")
        self.window.go(2); self.window.logs(); self.wait_idle(); self.assertIn('测试日志',self.window.log_output.toPlainText())
        self.window.go(3); self.assertFalse(self.window.terminal.connected)
        self.assertEqual(self.window.terminal_tabs.count(),1)
        self.window.new_terminal_tab(); self.assertEqual(self.window.terminal_tabs.count(),2)
        self.window.close_current_terminal_tab(); self.assertEqual(self.window.terminal_tabs.count(),1)
        self.assertFalse(hasattr(self.window,'terminal_start'))

    def test_terminal_tab_isolation_controls_and_close(self):
        from PySide6.QtWidgets import QPushButton
        first=self.window.terminal; first.clear_screen(); first.feed('first session')
        self.window.new_terminal_tab(); second=self.window.terminal
        second.clear_screen(); second.feed('second session')
        self.assertIsNot(first.process,second.process)
        clear=next(b for b in self.window.findChildren(QPushButton) if b.text()=='清空显示')
        clear.click()
        self.assertIn('first session',''.join(first.screen.display))
        self.assertNotIn('second session',''.join(second.screen.display))
        second.connected=True
        with patch.object(self.window,'ask',return_value=False): self.window.close_current_terminal_tab()
        self.assertEqual(self.window.terminal_tabs.count(),2)
        with patch.object(self.window,'ask',return_value=True): self.window.close_current_terminal_tab()
        self.assertIs(self.window.terminal,first)
        self.window.close_current_terminal_tab()
        self.assertEqual(self.window.terminal_tabs.count(),1)
        self.assertIsNot(self.window.terminal,first)

    def test_terminal_tabs_auto_connect_when_device_is_selected(self):
        self.api.adb.path=sys.executable
        with patch.object(Path,'is_file',return_value=True), patch.object(self.window.terminal,'connect_device') as connect:
            self.window.render_devices([{'serial':'usb-test','state':'device','transport':'USB'}])
            QTest.qWait(20)
            connect.assert_called_with(self.api.adb.path,'usb-test')
        with patch.object(Terminal,'connect_device') as connect:
            self.window.new_terminal_tab(); QTest.qWait(20)
            self.assertTrue(connect.called)

    def test_usb_refresh_syncs_lan_address(self):
        self.api.adb.path='test-adb'
        with patch.object(Path,'is_file',return_value=True), patch.object(Terminal,'connect_device'), patch.object(self.api.adb,'shell',return_value=(b'3: wlan0    inet 192.168.1.148/24 brd 192.168.1.255 scope global wlan0\n','',0)), patch.object(self.window,'work',side_effect=lambda fn,callback,*args,**kwargs:callback(fn())):
            self.window.render_devices([{'serial':'usb-test','state':'device','transport':'USB'}])
            self.window.sync_lan_address('usb-test')
            self.wait_idle()
        self.assertEqual(self.window.address.text(),'192.168.1.148:5555')

    def test_network_page_shows_usb_adapter(self):
        self.connect_fake(); self.window.go(5)
        with patch.object(self.api.adb,'shell',return_value=(b'eth1             UP             192.168.1.199/24\n\ndefault via 192.168.1.1 dev eth1\n','',0)), patch.object(self.window,'work',side_effect=lambda fn,callback,*args,**kwargs:callback(fn())):
            self.window.refresh_network_status(); self.wait_idle()
        self.assertEqual(self.window.net_table.rowCount(),1)
        self.assertEqual(self.window.net_table.item(0,1).text(),'USB 网卡')
        self.assertEqual(self.window.net_table.item(0,4).text(),'是')

    def test_background_responsiveness_and_failure(self):
        ticks=[]; QTimer.singleShot(20,lambda:ticks.append(True))
        self.window.work(lambda:time.sleep(.15),lambda data:None)
        QTest.qWait(80); self.assertTrue(ticks); self.assertTrue(self.window.busy)
        self.assertFalse(self.window.device_select.isEnabled()); self.wait_idle()
        def fail(): raise UserError('device offline')
        self.window.work(fail,lambda data:None); self.wait_idle()
        self.assertIn('device offline',self.window.banner.text()); self.assertTrue(self.window.device_select.isEnabled())

    def test_disallowed_device_selection(self):
        self.window.render_devices([{'serial':'bad','state':'unauthorized','transport':'USB'}])
        self.assertFalse(self.window.device_select.model().item(1).isEnabled()); self.assertEqual(self.window.serial,'')

    def test_download_preserves_existing_file_on_failure(self):
        self.connect_fake(); self.window.go(1); self.window.load_files(); self.wait_idle(); self.window.table.selectRow(1)
        with tempfile.TemporaryDirectory() as tmp:
            destination=Path(tmp)/'saved.txt'; destination.write_text('previous data')
            with patch('desktop.QFileDialog.getSaveFileName',return_value=(str(destination),'')), patch.object(self.api.adb,'shell',return_value=(b'4','',0)), patch.object(self.api.adb,'run',side_effect=UserError('transfer interrupted')):
                self.window.download(); self.wait_idle()
            self.assertEqual(destination.read_text(),'previous data')
            self.assertIn('transfer interrupted',self.window.banner.text())

    def test_upload_requires_confirmation(self):
        self.connect_fake(); self.window.go(1); self.window.load_files(); self.wait_idle()
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'test.txt'; source.write_text('hello')
            with patch('desktop.QFileDialog.getOpenFileName',return_value=(str(source),'')), patch.object(self.window,'ask',return_value=False), patch.object(self.api.adb,'run') as run:
                self.window.upload(); self.assertFalse(run.called)


if __name__=='__main__':
    if '--render' in sys.argv:
        app=QApplication([]); configure_app(app)
        window=Window(FakeApi(),autostart=False); window.timer.stop(); window.show(); QTest.qWait(80)
        root=Path(__file__).resolve().parent.parent
        window.grab().save(str(root/'界面预览.png'))
        window.render_devices([{'serial':'usb-test','state':'device','transport':'USB'}]); QTest.qWait(100); window.render_status(SAMPLE)
        second=SAMPLE.copy(); second.update(cpuTotal=1100,cpuIdle=870); window.render_status(second); QTest.qWait(30)
        window.grab().save(str(root/'界面预览-模拟设备.png'))
        for page,name in [(1,'files'),(2,'logs'),(3,'terminal'),(4,'services')]:
            window.go(page); QTest.qWait(30); window.grab().save(str(root/f'preview-{name}.png'))
        window.resize(1000,740); window.go(0); QTest.qWait(100); window.grab().save(str(root/'preview-small.png'))
        window.close()
    else: unittest.main()
