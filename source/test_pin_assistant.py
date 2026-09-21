import unittest
from unittest.mock import Mock

from adb_core import UserError
from pin_assistant import gpio, i2c_scan, inventory, pwm
from pin_widget import PINOUT_IMAGE, HEADER_GPIO_PINS, HEADER_I2C_BUSES, HEADER_SPI, HEADER_PWM, _linux_gpio


class PinAssistantTests(unittest.TestCase):
    def test_pinout_image_is_bundled_source_asset(self):
        self.assertTrue(PINOUT_IMAGE.is_file())
        self.assertGreater(PINOUT_IMAGE.stat().st_size, 10000)

    def test_header_gpio_mapping_uses_physical_pins(self):
        mapping = {physical: _linux_gpio(name) for physical, name, _ in HEADER_GPIO_PINS}
        self.assertEqual(mapping[8], 111)
        self.assertEqual(mapping[10], 112)
        self.assertEqual(mapping[37], 15)
        self.assertNotIn(1, mapping)
        self.assertNotIn(6, mapping)

    def test_header_bus_mappings_match_pinout(self):
        self.assertEqual(dict(HEADER_I2C_BUSES)['i2c-2'] if False else HEADER_I2C_BUSES[0][1], 'i2c-2')
        self.assertIn('排针 19/21/23/24', HEADER_SPI)
        self.assertEqual(HEADER_PWM[0][1], 'pwmchip2')

    def test_gpio_rejects_invalid_number_and_action(self):
        with self.assertRaises(UserError): gpio(Mock(), 'board', -1)
        with self.assertRaises(UserError): gpio(Mock(), 'board', '1;reboot')
        with self.assertRaises(UserError): gpio(Mock(), 'board', 4, 'shell')

    def test_gpio_read_is_bounded(self):
        adb = Mock(); adb.shell.return_value = (b'direction=input\nvalue=1\n', '', 0)
        result = gpio(adb, 'board', 4, 'read')
        self.assertEqual(result['value'], '1')
        script = adb.shell.call_args.args[1]
        self.assertIn('/sys/class/gpio/gpio4', script)
        self.assertNotIn(';reboot', script)

    def test_gpio_write_validates_level(self):
        with self.assertRaises(UserError): gpio(Mock(), 'board', 4, 'write', 2)

    def test_inventory_parses_marked_sections(self):
        raw = b'__MODEL__\nLCKFB TaishanPi V10\n__GPIOCHIPS__\ngpiochip0\n__I2C__\n/dev/i2c-3\n__I2CDEV__\ni2c-3\n__SPI__\nspidev1.0\n__PWM__\npwmchip0\n__PINMUX__\n## pins\n'
        adb = Mock(); adb.shell.return_value = (raw, '', 0)
        data = inventory(adb, 'board')
        self.assertEqual(data['model'], 'LCKFB TaishanPi V10')
        self.assertEqual(data['i2cdev'], ['i2c-3'])
        self.assertEqual(data['spi'], ['spidev1.0'])
        self.assertEqual(data['pwm'], ['pwmchip0'])

    def test_i2c_rejects_injection_and_missing_tool(self):
        with self.assertRaises(UserError): i2c_scan(Mock(), 'board', 'i2c-1; reboot')
        adb = Mock(); adb.shell.return_value = (b'I2CDETECT_MISSING\n', '', 4)
        with self.assertRaises(UserError): i2c_scan(adb, 'board', 'i2c-1')
        self.assertNotIn('reboot', adb.shell.call_args.args[1])

    def test_i2c_scan_returns_output(self):
        adb = Mock(); adb.shell.return_value = (b'     0 1 2\n00: -- 03 --\n', '', 0)
        result = i2c_scan(adb, 'board', 'i2c-2')
        self.assertEqual(result['bus'], 'i2c-2'); self.assertIn('03', result['output'])

    def test_pwm_bounds_and_status(self):
        with self.assertRaises(UserError): pwm(Mock(), 'board', 'pwmchip0', 0, 'configure', 100, 101, True)
        with self.assertRaises(UserError): pwm(Mock(), 'board', 'pwmchip0', 0, 'configure', 0, 0, True)
        adb = Mock(); adb.shell.return_value = (b'period=20000\nduty=10000\nenable=1\n', '', 0)
        result = pwm(adb, 'board', 'pwmchip0', 0)
        self.assertEqual(result['enable'], '1')
        self.assertIn('/sys/class/pwm/pwmchip0/pwm0', adb.shell.call_args.args[1])


if __name__ == '__main__': unittest.main()
