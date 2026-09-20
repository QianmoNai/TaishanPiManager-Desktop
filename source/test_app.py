import json
from pathlib import Path
import shlex
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

from app import Adb, App, Handler, UserError, endpoint, remote_path, parse_status


class FakeAdb(Adb):
    def __init__(self):
        super().__init__()
        self.path = 'test-adb'
        self.last = None

    def devices(self):
        return [{'serial':'usb-test','state':'device','model':'RK3566','transport':'USB'}]

    def shell(self, serial, script, timeout=12, check=True):
        self.last = script
        if 'stat -Lc' in script: return b'4\n','',0
        if 'count=0' in script:
            return b'd\x000\x001700000000\x00folder\x00f\x004\x001700000000\x00a;echo.txt\x00','',0
        return b'test output\n','',0

    def run(self, args, timeout=12, check=True):
        self.last = args
        if 'pull' in args: Path(args[-1]).write_bytes(b'data')
        return b'ok','',0


class BackendTests(unittest.TestCase):
    def test_status_parser(self):
        raw='\n@@identity\nLinux 6.1 aarch64\ntaishanpi\n@@os\nPRETTY_NAME="Buildroot 2024.02"\n@@uptime\n90061.0 0\n@@memory\nMemTotal: 1000 kB\nMemAvailable: 250 kB\n@@cpu\ncpu 10 0 20 60 10 0 0 0\n@@temperature\n45000\n@@disk\nFilesystem 1024-blocks Used Available Capacity Mounted on\n/dev/root 100 20 80 20% /\n@@network\neth0\n@@end\n'
        data=parse_status(raw)
        self.assertEqual(data['memoryUsed'],750*1024)
        self.assertEqual(data['cpuTotal'],100)
        self.assertEqual(data['cpuIdle'],70)
        self.assertEqual(data['os'],'Buildroot 2024.02')
        self.assertEqual(data['temperature'],45)
        self.assertEqual(data['disks'][0]['mount'],'/')

    def test_path_and_address_validation(self):
        for path in ['relative','/tmp/../etc','/a\nsh','/a\0b']:
            with self.assertRaises(UserError): remote_path(path)
        self.assertEqual(remote_path("/userdata/a';echo x"),"/userdata/a';echo x")
        for address in ['-x:5555','127.0.0.1:70000','x:5555;id','x']:
            with self.assertRaises(UserError): endpoint(address)

    def test_shell_quote_and_file_parser(self):
        fake=FakeAdb(); app=App(fake)
        path="/userdata/a'; echo hacked"
        result=app.dispatch('/api/files',{'serial':'usb-test','path':path})
        self.assertTrue(fake.last.startswith('cd '+shlex.quote(path)+' ||'))
        self.assertEqual(result['files'][1]['name'],'a;echo.txt')

    def test_confirm_and_device_lock(self):
        fake=FakeAdb(); app=App(fake)
        with self.assertRaises(UserError): app.dispatch('/api/reboot',{'serial':'usb-test'})
        with self.assertRaises(UserError): app.dispatch('/api/command',{'serial':'usb-test','command':'id'})
        lock=fake.lock('usb-test'); lock.acquire()
        try:
            with self.assertRaises(UserError): app.dispatch('/api/status',{'serial':'usb-test'})
        finally: lock.release()
        result=app.dispatch('/api/command',{'serial':'usb-test','command':'id','confirm':True})
        self.assertEqual(result['code'],0)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.app=App(FakeAdb())
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def post(self,path,data,headers=None):
        return urlopen(Request(self.base+path,data=json.dumps(data).encode(),headers=headers or {'X-App-Token':self.server.app.token,'Content-Type':'application/json'}))

    def test_auth_and_origin(self):
        with self.assertRaises(HTTPError) as err: self.post('/api/devices',{}, {'X-App-Token':'wrong'})
        self.assertEqual(err.exception.code,403)
        with self.assertRaises(HTTPError) as err: self.post('/api/devices',{}, {'X-App-Token':self.server.app.token,'Origin':'http://evil.test'})
        self.assertEqual(err.exception.code,403)
        with self.assertRaises(HTTPError) as err: self.post('/api/devices',{}, {'X-App-Token':self.server.app.token,'Host':'evil.test'})
        self.assertEqual(err.exception.code,403)

    def test_html_and_connected_flow(self):
        html=urlopen(self.base).read().decode()
        self.assertIn(self.server.app.token,html)
        self.assertIn('设备概览',html)
        result=json.load(self.post('/api/devices',{}))
        self.assertEqual(result['devices'][0]['transport'],'USB')
        response=self.post('/api/download',{'serial':'usb-test','path':'/userdata/test.txt'})
        self.assertEqual(response.read(),b'data')
        self.assertIn('test.txt',response.headers['Content-Disposition'])

    def test_upload_and_boundaries(self):
        req=Request(self.base+'/api/upload?serial=usb-test&path=/userdata/test.txt&confirm=yes',data=b'data',headers={'X-App-Token':self.server.app.token,'Content-Type':'application/octet-stream'})
        self.assertEqual(json.load(urlopen(req))['message'],'上传完成')
        with self.assertRaises(HTTPError): self.post('/api/reboot',{'serial':'usb-test'})


if __name__=='__main__': unittest.main()
