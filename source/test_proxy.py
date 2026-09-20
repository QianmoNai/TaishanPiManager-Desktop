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
        self.assertEqual(w.proxy.nodes.currentText(),'DIRECT')
        w.reset_data(); self.assertEqual(w.proxy.table.rowCount(),0)
        self.assertTrue(w.plugin_center.isVisible()); self.assertFalse(w.proxy.isVisible())


if __name__=='__main__': unittest.main()
