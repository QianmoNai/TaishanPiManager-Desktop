import re
import unittest
from unittest.mock import patch
from adb_core import Adb, UserError, parse_status


class TransportTests(unittest.TestCase):
    def test_nonzero_remote_exit_and_stdin(self):
        adb=Adb()
        def reply(args, timeout, check, input_data):
            self.assertEqual(args, ['-s','usb','shell','-T','sh','-s'])
            self.assertNotIn('private value',repr(args))
            self.assertIn(b'private value',input_data)
            marker=re.search(rb'__TSPI_EXIT_[0-9a-f]+__',input_data)[0]
            return b'failure\r\n'+marker+b'7\r\n','',0
        with patch.object(adb,'run',side_effect=reply):
            self.assertEqual(adb.shell('usb','echo private value',check=False),(b'failure','',7))
            with self.assertRaises(UserError): adb.shell('usb','echo private value')

    def test_missing_trailer_rejected(self):
        adb=Adb()
        with patch.object(adb,'run',return_value=(b'partial','',0)):
            with self.assertRaises(UserError): adb.shell('usb','true')

    def test_crlf_overview(self):
        raw='@@identity\r\nLinux 6.1 aarch64\r\ntaishanpi\r\n@@uptime\r\n12.0 10\r\n@@memory\r\nMemTotal: 2048 kB\r\nMemAvailable: 1024 kB\r\n@@cpu\r\ncpu 1 2 3 4 5 6 7 8\r\n@@end\r\n'
        data=parse_status(raw)
        self.assertEqual(data['hostname'],'taishanpi'); self.assertEqual(data['memoryTotal'],2048*1024)
        self.assertEqual(data['uptime'],12); self.assertEqual(data['cpuTotal'],36)


if __name__=='__main__': unittest.main()
