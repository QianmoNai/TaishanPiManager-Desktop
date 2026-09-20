import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from terminal_widget import Terminal


class TerminalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def setUp(self): self.term=Terminal(); self.term.resize(800,450); self.term.show(); QTest.qWait(20); self.term.clear_screen()
    def tearDown(self): self.term.disconnect_device(); self.term.close(); self.term.deleteLater(); QTest.qWait(20)
    def test_ansi_cursor_and_unicode(self):
        self.term.feed('\x1b[31m红色 OK\x1b[0m\r\nprogress 10%\rprogress 90%')
        self.assertEqual(self.term.screen.buffer[0][0].fg,'red')
        self.assertIn('红色 OK',self.term.screen.display[0])
        self.assertTrue(self.term.screen.display[1].startswith('progress 90%'))
    def test_alternate_screen_restores_normal_buffer(self):
        self.term.feed('normal\x1b[?1049hfullscreen\x1b[?1049l')
        self.assertTrue(self.term.screen.display[0].startswith('normal'))
        self.assertNotIn('fullscreen',''.join(self.term.screen.display))
    def test_history_is_bounded(self):
        self.term.feed('hello\r\n'*2500)
        self.assertLessEqual(len(self.term.screen.history.top),2000)
    def test_keyboard_interrupt_and_tab(self):
        self.term.connected=True
        with patch.object(self.term,'send') as send:
            QTest.keyClick(self.term,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(send.call_args.args[0],b'\x03')
            QTest.keyClick(self.term,Qt.Key.Key_Tab); self.assertEqual(send.call_args.args[0],b'\t')
            QTest.keyClick(self.term,Qt.Key.Key_Up); self.assertEqual(send.call_args.args[0],b'\x1b[A')
            QTest.keyClick(self.term,Qt.Key.Key_Return); self.assertEqual(send.call_args.args[0],b'\n')
            QTest.keyClick(self.term,Qt.Key.Key_F5); self.assertEqual(send.call_args.args[0],b'\x1b[15~')
    def test_paste_multiline_cancel_and_bracketed(self):
        self.term.connected=True; self.app.clipboard().setText('echo first\necho second')
        with patch('terminal_widget.QMessageBox.question',return_value=QMessageBox.StandardButton.No),patch.object(self.term,'send') as send:
            self.term.paste(); send.assert_not_called()
        self.app.clipboard().setText('hello'); self.term.feed('\x1b[?2004h')
        with patch.object(self.term,'send') as send:
            self.term.paste(); self.assertEqual(send.call_args.args[0],b'\x1b[200~hello\x1b[201~')
    def test_pty_marker_across_chunks(self):
        self.term.token='abc123'
        for part in ('hello\x1b]77','7;abc123;/dev/pts/','12\x07world'): self.term.consume(part)
        self.assertEqual(self.term.tty,'/dev/pts/12')
        self.assertTrue(self.term.screen.display[0].startswith('helloworld'))
        self.assertNotIn('abc123',''.join(self.term.screen.display))


if __name__=='__main__': unittest.main()
