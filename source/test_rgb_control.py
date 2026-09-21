import unittest
from unittest.mock import Mock

from rgb_control import set_color, status
from adb_core import UserError


class RgbControlTests(unittest.TestCase):
    def test_status_parses_all_channels(self):
        adb = Mock(); adb.shell.return_value = (b'r=1\ng=0\nb=1\n', '', 0)
        self.assertEqual(status(adb, 'board'), {'r': 1, 'g': 0, 'b': 1})

    def test_set_color_rejects_invalid_values(self):
        with self.assertRaises(UserError): set_color(Mock(), 'board', {'r': 2, 'g': 0, 'b': 0})

    def test_set_color_writes_each_channel(self):
        adb = Mock(); adb.shell.return_value = (b'', '', 0)
        self.assertEqual(set_color(adb, 'board', {'r': 1, 'g': 1, 'b': 0}), {'r': 1, 'g': 1, 'b': 0})
        script = adb.shell.call_args.args[1]
        self.assertIn('echo 1 > /sys/class/leds/rgb-led-r/brightness', script)
        self.assertIn('echo 1 > /sys/class/leds/rgb-led-g/brightness', script)
        self.assertIn('echo 0 > /sys/class/leds/rgb-led-b/brightness', script)


if __name__ == '__main__': unittest.main()
