import struct
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import zipfile
from adb_core import UserError
from ld06_plugin import Decoder, crc8, export_package, prepare

def frame(start=35000,end=1000):
    raw=b'\x54\x2c'+struct.pack('<HH',3600,start)
    raw+=b''.join(struct.pack('<HB',1000+i*100,100) for i in range(12))
    raw+=struct.pack('<HH',end,1234)
    return raw+bytes([crc8(raw)])

class RadarTests(unittest.TestCase):
    def test_split_packets_wrap_and_distances(self):
        decoder=Decoder(); data=frame()
        self.assertEqual(decoder.feed(b'noise'+data[:20]),[])
        points=decoder.feed(data[20:])
        self.assertEqual(len(points),12)
        self.assertAlmostEqual(points[0][0],350)
        self.assertAlmostEqual(points[-1][0],10)
        self.assertAlmostEqual(points[-1][1],2.1)
        self.assertEqual(decoder.speed,10)

    def test_crc_corruption_resync_and_bounded_noise(self):
        decoder=Decoder(); bad=bytearray(frame()); bad[10]^=1
        self.assertEqual(len(decoder.feed(bad+frame())),12)
        self.assertEqual(decoder.bad,1)
        decoder.feed(b'x'*100000)
        self.assertLessEqual(len(decoder.buffer),1)

    def test_optional_package_contains_helper_and_instructions(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'radar.zip'; export_package(path)
            with zipfile.ZipFile(path) as archive:
                self.assertIn('ld06-radar/ld06-helper.pl',archive.namelist())
                self.assertIn('ld06-radar/README.md',archive.namelist())

    def test_uninstalled_or_busy_port_is_not_opened(self):
        with patch('ld06_plugin.status',return_value={'state':'missing'}):
            with self.assertRaises(UserError): prepare(Mock(),'usb')
        with patch('ld06_plugin.status',return_value={'state':'installed'}), patch('ld06_plugin.inventory',return_value={'ports':[{'path':'/dev/ttyS3','reserved':'','owners':[123]}]}):
            with self.assertRaisesRegex(UserError,'占用'): prepare(Mock(),'usb')


if __name__=='__main__': unittest.main()
