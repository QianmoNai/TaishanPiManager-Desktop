import unittest
from camera_plugin import JpegFrames,stream_command
from adb_core import UserError
class CameraTests(unittest.TestCase):
 def test_split_and_newest_frame(self):
  p=JpegFrames();self.assertIsNone(p.feed(b'noise\xff'))
  self.assertEqual(p.feed(b'\xd8one\xff\xd9\xff\xd8two\xff\xd9'),b'\xff\xd8two\xff\xd9')
 def test_bounded_incomplete_frame(self):
  p=JpegFrames();p.feed(b'\xff\xd8'+b'x'*(9*1024*1024));self.assertLess(len(p.buffer),8*1024*1024)
 def test_validation_and_cleanup(self):
  with self.assertRaises(UserError):stream_command('/dev/video0;reboot',640,480,15,'mjpeg')
  cmd=stream_command('/dev/video0',640,480,15,'mjpeg')
  self.assertIn('image/jpeg',cmd);self.assertIn('/userdata/bin/tspi-camera-stream.sh',cmd)
