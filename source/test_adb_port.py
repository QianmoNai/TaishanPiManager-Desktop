import ast
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from adb_core import Adb, adb_arguments


class DedicatedPortTests(unittest.TestCase):
    def test_all_commands_use_loopback_port(self):
        adb = Adb(sys.executable)
        for args in (['devices', '-l'], ['connect', '192.168.1.150:5555'],
                     ['-s', 'device', 'push', 'local', '/tmp/file'],
                     ['-s', 'device', 'shell', 'echo OK']):
            with patch('adb_core.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as run:
                adb.run(args)
                self.assertEqual(run.call_args.args[0], [sys.executable, '-H', '127.0.0.1', '-P', '5038', *args])

    def test_helper_does_not_modify_arguments(self):
        args = ['devices']
        self.assertEqual(adb_arguments(args)[4:], args)
        self.assertEqual(args, ['devices'])

    def test_streaming_clients_share_argument_builder(self):
        # Catch future bypasses in every existing QProcess-based ADB stream.
        count = 0
        for name in ('terminal_widget.py', 'camera_widget.py', 'serial_widget.py', 'ld06_widget.py'):
            tree = ast.parse(Path(__file__).with_name(name).read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'setArguments':
                    self.assertIsInstance(node.args[0], ast.Call, name)
                    self.assertEqual(node.args[0].func.id, 'adb_arguments', name)
                    count += 1
        self.assertEqual(count, 5)
