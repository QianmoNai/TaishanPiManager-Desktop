import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
import test_desktop
from desktop import Window, configure_app
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QPlainTextEdit
from external_plugins import PluginStore


class ExecutionTests(unittest.TestCase):
    def test_confirmed_action_targets_selected_device(self):
        app = QApplication.instance() or QApplication([])
        configure_app(app)
        window = Window(test_desktop.FakeApi(), autostart=False)
        window.serial = 'fake-target'
        window.api.adb.run = Mock(return_value=(b'example output', '', 0))
        window.work = lambda fn, callback, *args: callback(fn())
        errors = []
        with tempfile.TemporaryDirectory() as root:
            plugin = window.plugin_center.external
            plugin.store = PluginStore(root)
            package = Path(__file__).resolve().parent.parent / 'dist/v2.53/device-info.zip'
            plugin.store.install(package)
            def accept_preview():
                preview = app.activeModalWidget()
                preview.accept()
            def activate():
                dialog = app.activeModalWidget()
                try:
                    QTimer.singleShot(0, accept_preview)
                    next(b for b in dialog.findChildren(QPushButton) if b.text() == '查询设备信息').click()
                    self.assertIn('example output', dialog.findChild(QPlainTextEdit).toPlainText())
                except Exception as exc:
                    errors.append(exc)
                finally:
                    dialog.accept()
            QTimer.singleShot(0, activate)
            plugin.open(plugin.store.packages()[0][0])
            self.assertEqual(errors, [])
            args, kwargs = window.api.adb.run.call_args
            self.assertEqual(args[0][:2], ['-s', 'fake-target'])
            self.assertIn(b'uname -a', kwargs['input_data'])
        window.close()
