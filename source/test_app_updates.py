import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
import unittest
from unittest.mock import patch
from PySide6.QtCore import QObject, Signal, QUrl
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QApplication
from app_updates import APP_VERSION, API_URL, MAX_RESPONSE, RELEASES_URL, UpdateStatus as UpdateDialog, parse_release, version_tuple


def payload(tag='v2.67-release', **kwargs):
    data = dict(tag_name=tag, prerelease=False, body='测试更新说明')
    data.update(kwargs)
    return json.dumps(data).encode()


class FakeReply(QObject):
    readyRead = Signal()
    finished = Signal()

    def __init__(self, raw=b'', code=200):
        super().__init__()
        self.raw, self.code, self.aborted = raw, code, False

    def setReadBufferSize(self, size): pass
    def read(self, size):
        data, self.raw = self.raw[:size], self.raw[size:]
        return data
    def attribute(self, _): return self.code
    def error(self): return QNetworkReply.NetworkError.NoError
    def abort(self):
        self.aborted = True
        self.finished.emit()


class ReleaseTests(unittest.TestCase):
    def test_gitee_without_draft_field(self):
        data = json.loads(payload())
        self.assertNotIn('draft', data)
        self.assertTrue(parse_release(json.dumps(data).encode())['newer'])
        data['draft'] = False
        self.assertTrue(parse_release(json.dumps(data).encode())['newer'])

    def test_gitee_endpoints(self):
        self.assertEqual(API_URL, 'https://gitee.com/api/v5/repos/qianmonai/TaishanPiManager-Desktop/releases/latest')
        self.assertEqual(RELEASES_URL, 'https://gitee.com/qianmonai/TaishanPiManager-Desktop/releases')

    def test_numeric_order_and_release_suffix(self):
        self.assertGreater(version_tuple('v2.10'), version_tuple('v2.9'))
        self.assertEqual(version_tuple('v2.63-release'), version_tuple('2.63.0'))

    def test_new_equal_and_older(self):
        self.assertTrue(parse_release(payload())['newer'])
        self.assertFalse(parse_release(payload('v'+APP_VERSION))['newer'])
        self.assertFalse(parse_release(payload('v2.62-release'))['newer'])

    def test_invalid_metadata(self):
        for raw in (b'bad', b'[]', payload(prerelease=True), payload(draft=True),
                    payload('v2.64-beta'), payload('desktop-initial'), payload(body=42),
                    b'x'*(MAX_RESPONSE+1)):
            with self.subTest(raw=raw[:60]):
                with self.assertRaises(ValueError): parse_release(raw)

    def test_url_not_controlled_by_remote(self):
        result = parse_release(payload(html_url='file:///evil', body='<script>test</script>'))
        self.assertEqual(result['url'], RELEASES_URL+'/tag/v2.67-release')
        self.assertEqual(result['notes'], '<script>test</script>')


class UpdateDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def exercise(self, raw, code=200):
        dialog = UpdateDialog()
        reply = FakeReply(raw, code)
        with patch.object(dialog.manager, 'get', return_value=reply) as get:
            dialog.check()
            dialog.check()
            self.assertEqual(get.call_count, 1)
            request = get.call_args.args[0]
            self.assertEqual(request.url().toString(), API_URL)
            self.assertEqual(bytes(request.rawHeader('Accept')), b'application/json')
            self.assertFalse(request.hasRawHeader('Authorization'))
            self.assertFalse(dialog.check_button.isEnabled())
            reply.readyRead.emit()
            if dialog.reply is not None: reply.finished.emit()
        self.assertTrue(dialog.check_button.isEnabled())
        self.assertFalse(dialog.deadline.isActive())
        return dialog

    def test_success_plain_text(self):
        dialog = self.exercise(payload(body='<b>更新</b>'))
        self.assertIn('发现新版本', dialog.status.text())
        self.assertEqual(dialog.notes.toPlainText(), '<b>更新</b>')
        dialog.reject()

    def test_safe_redirect_and_limit(self):
        dialog = UpdateDialog()
        reply = FakeReply(b'', 302)
        reply.url = lambda: QUrl(API_URL)
        reply.attribute = lambda key: (302 if key == QNetworkRequest.Attribute.HttpStatusCodeAttribute
                                        else QUrl(API_URL + '/'))
        with patch.object(dialog.manager, 'get', return_value=reply) as get:
            dialog.check()
            for _ in range(4):
                dialog.complete()
            self.assertEqual(get.call_count, 4)
        self.assertIn('重定向次数过多', dialog.notes.toPlainText())
        self.assertTrue(dialog.check_button.isEnabled())
        dialog.reject()

    def test_block_untrusted_redirects(self):
        for target in ('http://gitee.com/x', 'https://evil.example/x',
                       'https://gitee.com/login', API_URL+'?access_token=secret'):
            dialog = UpdateDialog()
            reply = FakeReply(b'', 302)
            reply.url = lambda: QUrl(API_URL)
            reply.attribute = lambda key: (302 if key == QNetworkRequest.Attribute.HttpStatusCodeAttribute
                                            else QUrl(target))
            with patch.object(dialog.manager, 'get', return_value=reply) as get:
                dialog.check(); reply.finished.emit()
                self.assertEqual(get.call_count, 1)
            self.assertIn('非预期地址', dialog.notes.toPlainText())
            self.assertNotIn('secret', dialog.notes.toPlainText())
            dialog.reject()

    def test_tls_diagnostic(self):
        dialog = UpdateDialog()
        reply = FakeReply(b'', None)
        reply.error = lambda: QNetworkReply.NetworkError.SslHandshakeFailedError
        with patch.object(dialog.manager, 'get', return_value=reply):
            dialog.check(); reply.finished.emit()
        self.assertIn('TLS', dialog.notes.toPlainText())
        self.assertIn('SslHandshakeFailedError', dialog.notes.toPlainText())
        self.assertIn('未收到响应', dialog.notes.toPlainText())
        dialog.reject()

    def test_failures_not_reported_as_up_to_date(self):
        for raw, code in [(b'', 403), (b'', 429), (b'', 404), (b'', 500),
                          (b'html', 200), (b'x'*(MAX_RESPONSE+1), 200)]:
            dialog = self.exercise(raw, code)
            self.assertIn('检查失败', dialog.status.text())
            dialog.reject()

    def test_timeout_and_retry(self):
        dialog = UpdateDialog()
        reply = FakeReply()
        with patch.object(dialog.manager, 'get', return_value=reply):
            dialog.check()
            dialog.deadline.timeout.emit()
        self.assertTrue(reply.aborted)
        self.assertIn('超时', dialog.status.text())
        self.assertTrue(dialog.check_button.isEnabled())
        second = FakeReply(payload('v2.62-release'))
        with patch.object(dialog.manager, 'get', return_value=second):
            dialog.check()
            second.finished.emit()
        self.assertEqual('暂无更新', dialog.status.text())
        self.assertEqual(dialog.failure, '')
        dialog.reject()

    def test_sidebar_single_dialog_and_main_close(self):
        from desktop import Window, configure_app
        from test_desktop import FakeApi
        configure_app(self.app)
        window = Window(FakeApi(), autostart=False)
        reply = FakeReply(payload())
        with patch.object(window.update_status.manager, 'get', return_value=reply) as get:
            window.update_btn.click()
            dialog = window.update_status
            window.update_btn.click()
            self.assertIs(window.update_status, dialog)
            self.assertEqual(get.call_count, 1)
            self.assertFalse(dialog.isWindow())
            self.assertFalse(dialog.isHidden())
            self.assertTrue(dialog.notes.isHidden())
            window.close()
            self.assertTrue(dialog.closed)
            self.assertTrue(reply.aborted)
        window.deleteLater()

    def test_close_cancels(self):
        dialog = UpdateDialog()
        reply = FakeReply()
        with patch.object(dialog.manager, 'get', return_value=reply): dialog.check()
        dialog.reject()
        self.assertTrue(reply.aborted)
        self.assertIsNone(dialog.reply)
        self.assertFalse(dialog.deadline.isActive())

    def test_browser_only_on_click(self):
        with patch('app_updates.QDesktopServices.openUrl', return_value=True) as open_url:
            dialog = self.exercise(payload())
            open_url.assert_not_called()
            dialog.release_button.click()
            self.assertEqual(open_url.call_args.args[0].toString(), RELEASES_URL+'/tag/v2.67-release')
            dialog.reject()


if __name__ == '__main__': unittest.main()
