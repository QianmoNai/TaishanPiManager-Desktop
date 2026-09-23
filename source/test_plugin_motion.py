import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from PySide6.QtCore import QPropertyAnimation, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QWidget
from desktop import button, card, configure_app, label
from external_plugin_ui import CardMotion, ExternalPlugins


class PluginMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        configure_app(cls.app)

    def test_motion_reuses_objects_and_disables_idle_effects(self):
        dialog = QDialog()
        widget = QLabel('42', dialog)
        motion = CardMotion(dialog)
        motion.reveal(widget)
        dialog.show()
        QTest.qWait(350)
        effect, animation, timer = motion.entries[widget]
        self.assertFalse(effect.isEnabled())
        for _ in range(10):
            motion.pulse(widget)
            QTest.qWait(240)
        self.assertEqual(len(motion.findChildren(QPropertyAnimation)), 1)
        self.assertEqual(len(motion.findChildren(QTimer)), 1)
        self.assertFalse(effect.isEnabled())
        dialog.reject()
        self.assertTrue(motion.closed)
        motion.pulse(widget)
        self.assertFalse(effect.isEnabled())
        dialog.deleteLater()

    def test_close_cancels_delayed_reveal(self):
        dialog = QDialog()
        widget = QLabel('pending', dialog)
        motion = CardMotion(dialog)
        motion.reveal(widget, 100)
        dialog.show()
        dialog.reject()
        QTest.qWait(150)
        effect, animation, timer = motion.entries[widget]
        self.assertFalse(timer.isActive())
        self.assertFalse(effect.isEnabled())
        self.assertEqual(effect.opacity(), 1.0)
        self.assertEqual(animation.state(), QPropertyAnimation.State.Stopped)
        dialog.deleteLater()

    def test_page_opens_updates_and_ignores_late_result(self):
        center = QWidget()
        callbacks = []
        center.owner = SimpleNamespace(
            serial='fake-device', busy=False, require_device=lambda: True,
            work=lambda fn, callback, *args, **kwargs: callbacks.append(callback))
        plugin = ExternalPlugins(center, label, button, card)
        plugin.store = Mock()
        manifest = dict(id='org.test.motion', name='Motion', description='Test page', actions=[],
                        page=dict(title='Motion', cards=[dict(id='cpu', title='CPU')],
                                  poll=dict(script='status.sh', interval_ms=60000)))
        errors = []

        def check_page():
            dialog = self.app.activeModalWidget()
            try:
                self.assertIsNotNone(dialog)
                motion = dialog.findChild(CardMotion)
                self.assertEqual(len(motion.entries), 4)
                callbacks[0]((b'{"cpu": 42}', '', 0))
                metric = next(w for w in dialog.findChildren(QLabel) if w.text() == '42')
                frame = metric.parentWidget()
                self.assertTrue(frame.graphicsEffect().isEnabled())
                QTest.qWait(240)
                for _ in range(100):
                    callbacks[0]((b'{"cpu": 42}', '', 0))
                self.assertFalse(frame.graphicsEffect().isEnabled())
                self.assertEqual(len(motion.findChildren(QPropertyAnimation)), 4)
                callbacks[0]((b'{"cpu": 43}', '', 0))
                self.assertEqual(metric.text(), '43')
                self.assertTrue(frame.graphicsEffect().isEnabled())
                dialog.reject()
                callbacks[0]((b'{"cpu": 99}', '', 0))
                self.assertEqual(metric.text(), '43')
            except Exception as exc:
                errors.append(exc)
            finally:
                if dialog is not None:
                    dialog.reject()

        QTimer.singleShot(650, check_page)
        plugin.open_page(None, manifest, {'status.sh': 'unused'})
        self.assertEqual(errors, [])
        self.assertEqual(len(callbacks), 1)
        center.deleteLater()


if __name__ == '__main__':
    unittest.main()
