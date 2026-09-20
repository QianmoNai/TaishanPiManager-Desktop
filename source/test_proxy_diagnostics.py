import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch,MagicMock
import proxy_diagnostics as diag
from adb_core import UserError
from test_proxy import ProxyUiTests
from PySide6.QtTest import QTest

class DiagnosticTests(unittest.TestCase):
    def test_http_uses_forward_not_direct_and_follows_redirect(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        import threading
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                seen.append((self.path,self.headers.get('Proxy-Authorization')))
                if self.path.endswith('/first'):
                    self.send_response(302); self.send_header('Location','http://remote.invalid/final'); self.end_headers()
                else: self.send_response(204); self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler); t=threading.Thread(target=server.serve_forever,daemon=True); t.start()
        try:
            status,body,delay=diag.fetch(server.server_port,{'Proxy-Authorization':'Basic synthetic'},'http://remote.invalid/first')
            self.assertEqual(status,204); self.assertGreater(delay,0)
            self.assertEqual(seen,[('http://remote.invalid/first','Basic synthetic'),('http://remote.invalid/final','Basic synthetic')])
        finally: server.shutdown(); server.server_close(); t.join()

    def test_https_tunnels_and_does_not_send_proxy_credentials_to_origin(self):
        with patch.object(diag.http.client,'HTTPSConnection') as cls:
            conn=cls.return_value; conn.getresponse.return_value.status=200; conn.getresponse.return_value.read.return_value=b'ok'
            diag.fetch(12345,{'Proxy-Authorization':'Basic synthetic'},'https://remote.invalid/path?q=1')
            cls.assert_called_once(); self.assertEqual(cls.call_args.args,('127.0.0.1',12345))
            conn.set_tunnel.assert_called_once_with('remote.invalid',443,headers={'Proxy-Authorization':'Basic synthetic'})
            self.assertNotIn('Proxy-Authorization',conn.request.call_args.kwargs['headers'])
            self.assertEqual(conn.request.call_args.args[:2],('GET','/path?q=1')); conn.close.assert_called_once()

    def test_session_cleanup_and_ip_fields(self):
        import proxy_plugin
        adb=MagicMock(); adb.shell.return_value=(b'{"authentication":["u:p"]}','',0); adb.run.return_value=(b'12345','',0)
        with patch.object(proxy_plugin,'owned'),patch.object(proxy_plugin,'status',return_value={'state':'running'}):
            with self.assertRaises(RuntimeError):
                with diag.proxy_session(adb,'usb') as (port,headers):
                    self.assertEqual(port,12345); self.assertIn('Proxy-Authorization',headers); raise RuntimeError()
        self.assertEqual(adb.run.call_args.args[0],['-s','usb','forward','--remove','tcp:12345'])
        @contextmanager
        def session(*args): yield 12345,{}
        raw=json.dumps({'success':True,'ip':'203.0.113.1','country':'Example','connection':{'asn':64500,'isp':'Example ISP'},'timezone':{'id':'Asia/Tokyo'}}).encode()
        with patch.object(diag,'proxy_session',session),patch.object(diag,'fetch',return_value=(200,raw,35)):
            info=diag.ip_info(None,'usb'); self.assertEqual(info['ip'],'203.0.113.1'); self.assertEqual(info['asn'],'64500')
        with patch.object(diag,'proxy_session',session),patch.object(diag,'fetch',return_value=(200,b'{"success":false}',35)),self.assertRaises(UserError): diag.ip_info(None,'usb')

    def test_sites_distinguish_http_rejection_from_transport_failure(self):
        @contextmanager
        def session(*args): yield 12345,{}
        def fetch(port,headers,url):
            if url.endswith('/bad'): raise OSError('private detail')
            return (403,b'',20) if url.endswith('/blocked') else (204,b'',12)
        with patch.object(diag,'proxy_session',session),patch.object(diag,'fetch',fetch):
            result=diag.websites(None,'usb',{'urls':['https://example.org/good','https://example.org/bad','https://example.org/blocked']})['sites']
        self.assertEqual([result['https://example.org/'+x]['state'] for x in ('good','bad','blocked')],['ok','failed','http_error'])
        for url in ('file:///tmp/test','https://u:p@example.org','http://[broken'):
            with self.assertRaises(UserError): diag.checked_url(url)

class DiagnosticUiTests(ProxyUiTests):
    def test_network_name_and_stale_results(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        w=self.window; self.assertEqual(next(c['name'] for c in w.plugin_center.cards if c['key']=='wifi'),'网络设置')
        w.go(4,False); w.open_proxy(); w.serial='usb'; d=w.proxy.diagnostics
        w.job=SimpleNamespace(signals=SimpleNamespace(error=Mock()))
        with patch.object(w,'work') as work:
            d.refresh_ip(); done=work.call_args.args[1]
            done({'ip':'203.0.113.1','time':'12:00:00','country':'Example'})
            self.assertNotIn('203.0.113.1',d.ip_text.text()); d.toggle_ip(); self.assertIn('203.0.113.1',d.ip_text.text())
            d.refresh_ip(); stale=work.call_args.args[1]; d.reset(); stale({'ip':'203.0.113.2','time':'12:00:01'})
            self.assertEqual(d.address,''); self.assertFalse(d.auto.isChecked())
        w.proxy.hide(); d.auto.setChecked(True)
        with patch.object(d,'refresh_ip') as refresh: d.auto_refresh(); refresh.assert_not_called()

if __name__=='__main__': unittest.main()
