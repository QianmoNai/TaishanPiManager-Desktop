import json
import unittest
from unittest.mock import Mock
from test_desktop import DesktopTests


class ReceiveLoadTests(DesktopTests):
    def test_sustained_no_newline_and_hex(self):
        panel = self.window.serial_tool
        original = panel.process
        panel.process = Mock()
        try:
            for mode in (0, 1):
                panel.clear()
                panel.view.setCurrentIndex(mode)
                payload = b'x' * 16384
                event = (json.dumps({'event': 'rx', 'hex': payload.hex()}) + '\n').encode()
                panel.process.readAllStandardOutput.return_value = event
                for _ in range(150):
                    panel.drain()
                    panel.flush_display()
                    self.app.processEvents()
                    self.assertLessEqual(panel.receive.document().characterCount(), 65537)
                self.assertEqual(panel.rx, 150 * len(payload))
                self.assertGreater(panel.display_dropped, 0)
                self.assertLessEqual(max(map(len, panel.receive.toPlainText().splitlines())), 256)
            panel.clear()
            self.assertEqual(panel.display_dropped, 0)
        finally:
            panel.process = original
