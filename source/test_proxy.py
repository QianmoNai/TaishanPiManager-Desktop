import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from adb_core import UserError
import proxy_plugin as proxy
import test_desktop


class FakeAdb:
    def __init__(self): self.scripts=[]; self.calls=[]
    def shell(self,serial,script,**kw):
        self.scripts.append(script)
        if ' status' in script: return b'stopped\nconfigured\n','',0
        if 'validation.log' in script and ' -t ' in script: return b'','',1
        return b'','',0
    def run(self,args,**kw): self.calls.append(args); return b'54321\n','',0


class ProxyTests(unittest.TestCase):
    def test_config_controller_is_private_and_explicit_proxy_only(self):
        raw=json.dumps({'mixed-port':8888,'external-controller':'0.0.0.0:80','secret':'example', 'tun':{'enable':True},'dns':{'enable':True},'redir-port':1234,'rules':['MATCH,DIRECT']})
        config=proxy.normalize_config(raw)
        self.assertEqual(config['mixed-port'],7890)
        self.assertFalse(config['allow-lan']); self.assertFalse(config['tun']['enable'])
        self.assertFalse(config['dns']['enable']); self.assertNotIn('redir-port',config)
        self.assertEqual(config['external-controller'],'127.0.0.1:9090')
        self.assertNotEqual(config['secret'],'example')
        self.assertEqual(config['rules'],['MATCH,DIRECT'])
        self.assertTrue(proxy.normalize_config(raw,True)['allow-lan'])

    def test_invalid_config_and_provider_escape_rejected(self):
        for raw in ('[one,two]', '!!python/object/apply:os.system [echo forbidden]', 'a: &a [*a]', '{"proxy-providers":{"x":{"path":"../outside"}}}'):
            with self.assertRaises(UserError): proxy.normalize_config(raw)

    def test_import_validation_failure_leaves_active_config_untouched(self):
        adb=FakeAdb()
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'config.yaml'; f.write_text('rules: [MATCH,DIRECT]')
            with self.assertRaisesRegex(UserError,'原配置未修改'):
                proxy.import_config(adb,'usb',{'filename':str(f)})
        self.assertFalse(any('mv ' in s for s in adb.scripts))
        self.assertIn('rmdir',adb.scripts[-1])

    def test_installer_rejects_without_confirmation(self):
        adb=FakeAdb()
        with self.assertRaises(UserError): proxy.install(adb,'usb',{})
        self.assertEqual(adb.scripts,[])

    def test_controller_releases_forward_on_failure_without_secret_leak(self):
        adb=FakeAdb()
        with patch.object(proxy,'owned'),patch.object(proxy,'status',return_value={'state':'running'}),patch.object(adb,'shell',return_value=(json.dumps({'secret':'test-private-token'}).encode(),'',0)),patch('urllib.request.build_opener',side_effect=RuntimeError('test-private-token')):
            with self.assertRaises(UserError) as error: proxy.controller(adb,'usb','GET','/configs')
        self.assertNotIn('test-private-token',str(error.exception))
        self.assertEqual(adb.calls[-1],['-s','usb','forward','--remove','tcp:54321'])


class ProxyUiTests(unittest.TestCase):
    setUpClass=classmethod(test_desktop.DesktopTests.setUpClass.__func__)
    setUp=test_desktop.DesktopTests.setUp
    tearDown=test_desktop.DesktopTests.tearDown
    wait_idle=test_desktop.DesktopTests.wait_idle

    def test_catalog_navigation_and_device_reset(self):
        w=self.window
        self.assertEqual(w.net_btn.text(),'无线连接')
        self.assertEqual(len(w.plugin_center.cards),6)
        w.go(4,False); w.open_proxy()
        self.assertTrue(w.proxy.isVisible()); self.assertFalse(w.plugin_center.isVisible())
        w.proxy.render({'groups':[{'name':'group','type':'Selector','now':'DIRECT','all':['DIRECT','REJECT']}],'mode':'global'})
        self.assertEqual([c.name for c in w.proxy.grid.cards],['DIRECT','REJECT'])
        self.assertTrue(w.proxy.grid.cards[0].property('selected'))
        w.reset_data(); self.assertEqual(len(w.proxy.grid.cards),0)
        self.assertTrue(w.plugin_center.isVisible()); self.assertFalse(w.proxy.isVisible())


import threading
import time
from contextlib import contextmanager
from urllib.parse import quote
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

class DelayTests(unittest.TestCase):
    def test_snapshot_preserves_nodes_and_history(self):
        result=proxy.proxy_snapshot({'pick':{'type':'Selector','all':['a','b'],'now':'a'},'a':{'type':'Shadowsocks','udp':True,'history':[{'delay':51,'time':'now'}]},'b':{'history':[{'delay':0}]}},'rule')
        self.assertEqual(result['groups'][0]['now'],'a')
        self.assertEqual(result['nodes']['a']['delay'],51)
        self.assertTrue(result['nodes']['a']['alive'])
        self.assertTrue(result['nodes']['b']['tested'])
        self.assertFalse(result['nodes']['b']['alive'])

    def test_delay_encoding_and_distinct_states(self):
        paths=[]
        @contextmanager
        def session(*args):
            def request(method,path):
                paths.append(path)
                if 'offline' in path: raise proxy.DelayUnavailable()
                return {'delay':83}
            yield request
        with patch.object(proxy,'controller_session',session):
            data=proxy.test_delays(None,'usb',{'names':['香港 / #1','offline','REJECT']})
        self.assertEqual(data['delays']['香港 / #1']['delay'],83)
        self.assertEqual(data['delays']['offline']['state'],'failed')
        self.assertEqual(data['delays']['REJECT']['state'],'policy')
        self.assertEqual(len(paths),2)
        self.assertIn('/proxies/'+quote('香港 / #1',safe='')+'/delay?',paths[0])
        self.assertIn('timeout=5000',paths[0])

    def test_controller_error_aborts_batch(self):
        @contextmanager
        def session(*args):
            def request(*args): raise UserError('controller unavailable')
            yield request
        with patch.object(proxy,'controller_session',session),self.assertRaises(UserError):
            proxy.test_delays(None,'usb',{'names':['a']})

    def test_batch_and_url_validation(self):
        for data in ({'names':['a']*5},{'names':['a'],'url':'file:///etc/passwd'},{'names':['a'],'url':'https://user:pass@host/'},{'names':['a'],'url':'http://[invalid'}):
            with self.assertRaises(UserError): proxy.test_delays(None,'usb',data)

    def test_concurrent_batch_uses_one_session(self):
        active=0; peak=0; sessions=0; lock=threading.Lock()
        @contextmanager
        def session(*args):
            nonlocal sessions
            sessions+=1
            def request(*args):
                nonlocal active,peak
                with lock: active+=1; peak=max(peak,active)
                time.sleep(.03)
                with lock: active-=1
                return {'delay':20}
            yield request
        with patch.object(proxy,'controller_session',session): proxy.test_delays(None,'usb',{'names':['a','b','c','d']})
        self.assertEqual(sessions,1); self.assertGreater(peak,1); self.assertLessEqual(peak,4)

class NodeUiTests(ProxyUiTests):
    def setup_nodes(self):
        p=self.window.proxy
        self.window.go(4,False); self.window.open_proxy(); self.window.serial='test-usb'
        p.render({'groups':[{'name':'手动选择','type':'Selector','now':'A','all':['A','B','C','REJECT']},{'name':'自动','type':'URLTest','now':'A','all':['A','B']}],'nodes':{'A':{'type':'SS'},'B':{'type':'VMess'},'C':{'type':'Trojan'}},'mode':'rule'})
        return p

    def test_click_selection_and_filter_sort(self):
        p=self.setup_nodes()
        with patch.object(self.window,'work') as work:
            QTest.mouseClick(p.grid.cards[1],Qt.MouseButton.LeftButton)
            self.assertTrue(p.grid.cards[0].property('selected'))
            fn,done,_=work.call_args.args; fn()
            self.assertEqual(self.api.calls[-1],('/api/proxy-select',{'serial':'test-usb','group':'手动选择','node':'B'}))
            done({'output':'节点已切换。'})
        self.assertTrue(p.grid.cards[1].property('selected'))
        p.delays={'A':{'state':'ok','delay':300},'B':{'state':'ok','delay':30},'C':{'state':'failed'}}
        p.sort.setCurrentIndex(1); self.assertEqual([c.name for c in p.grid.cards][:2],['B','A'])
        p.search.setText('Trojan'); self.assertEqual([c.name for c in p.grid.cards],['C'])
        p.search.clear(); p.group.setCurrentIndex(1)
        with patch.object(p,'action') as action: p.select_node('B'); action.assert_not_called()

    def test_probe_button_does_not_select(self):
        from PySide6.QtWidgets import QPushButton
        p=self.setup_nodes()
        with patch.object(p,'start_tests') as probe,patch.object(p,'action') as select:
            p.grid.cards[1].findChild(QPushButton,'nodeTest').click()
            probe.assert_called_once_with(['B']); select.assert_not_called()

    def test_cancel_and_reset_ignore_stale_batch(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        p=self.setup_nodes(); self.window.job=SimpleNamespace(signals=SimpleNamespace(error=Mock()))
        with patch.object(self.window,'work') as work:
            p.start_tests(['A','B','C','D','E']); self.assertEqual(p.queue,['E'])
            done=work.call_args.args[1]; p.cancel_tests(); done({'delays':{'A':{'state':'ok','delay':40}}})
            QTest.qWait(10); self.assertEqual(work.call_count,1)
            p.start_tests(['A']); stale=work.call_args.args[1]; p.reset(); stale({'delays':{'A':{'state':'ok','delay':40}}})
            self.assertEqual(p.delays,{}); self.assertFalse(p.testing)

class ControllerHttpTests(unittest.TestCase):
    def test_http_delay_timeout_auth_and_forward_cleanup(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                if self.headers.get('Authorization')!='Bearer synthetic-token':
                    self.send_response(401); self.end_headers(); return
                self.send_response(503 if 'offline' in self.path else 401 if 'denied' in self.path else 200)
                self.end_headers(); self.wfile.write(b'{"delay":42}')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        adb=FakeAdb()
        def run(args,**kwargs): adb.calls.append(args); return str(server.server_port).encode(),'',0
        try:
            with patch.object(proxy,'owned'),patch.object(proxy,'status',return_value={'state':'running'}),patch.object(adb,'shell',return_value=(b'{"secret":"synthetic-token"}','',0)),patch.object(adb,'run',side_effect=run):
                result=proxy.test_delays(adb,'usb',{'names':['online','offline']})
                self.assertEqual(result['delays']['online']['delay'],42)
                self.assertEqual(result['delays']['offline']['state'],'failed')
                with self.assertRaises(UserError): proxy.test_delays(adb,'usb',{'names':['denied']})
            self.assertEqual(sum('--remove' in c for c in adb.calls),2)
        finally: server.shutdown(); server.server_close(); thread.join()

if __name__=='__main__': unittest.main()
