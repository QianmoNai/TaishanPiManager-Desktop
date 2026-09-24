import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
import unittest
from unittest.mock import patch
from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QApplication
from app_updates import APP_VERSION, MAX_RESPONSE, RELEASES_URL, UpdateDialog, parse_release, version_tuple


def payload(tag='v2.64-release', **kwargs):
    data = dict(tag_name=tag, draft=False, prerelease=False, body='测试更新说明')
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
        self.assertEqual(result['url'], RELEASES_URL+'/tag/v2.64-release')
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
        self.assertIn('未发现比当前程序更新', dialog.status.text())
        self.assertEqual(dialog.failure, '')
        dialog.reject()

    def test_sidebar_single_dialog_and_main_close(self):
        from desktop import Window, configure_app
        from test_desktop import FakeApi
        configure_app(self.app)
        window = Window(FakeApi(), autostart=False)
        with patch.object(UpdateDialog, 'check') as check:
            window.update_btn.click()
            dialog = window.update_dialog
            window.update_btn.click()
            self.assertIs(window.update_dialog, dialog)
            self.assertEqual(check.call_count, 1)
            window.close()
            self.assertIsNone(window.update_dialog)
            self.assertTrue(dialog.closed)
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
            self.assertEqual(open_url.call_args.args[0].toString(), RELEASES_URL+'/tag/v2.64-release')
            dialog.reject()


if __name__ == '__main__': unittest.main()
