"""V4L2 discovery and bounded JPEG streaming over ADB."""
import re, shlex, hashlib, secrets, sys
from pathlib import Path
ASSETS=(Path(sys._MEIPASS) if getattr(sys,"frozen",False) else Path(__file__).resolve().parent.parent)/"plugins/camera-assistant"
TARGET="/userdata/bin/tspi-camera-stream.sh"
from adb_core import UserError

def inventory(adb,serial):
 script=r'''command -v gst-launch-1.0 >/dev/null && echo GST:yes
for d in /dev/video[0-9]*; do
 [ -c "$d" ] || continue
 echo DEVICE:$d
 v4l2-ctl -d "$d" --all 2>/dev/null
 v4l2-ctl -d "$d" --list-formats-ext 2>/dev/null
done
'''
 out,_,_=adb.shell(serial,script,timeout=20,check=False)
 devices=[]; cur=None; gst=False
 for line in out.decode('utf-8','replace').splitlines():
  if line=='GST:yes': gst=True
  elif line.startswith('DEVICE:'): cur={'path':line[7:],'info':[]}; devices.append(cur)
  elif cur and line.strip(): cur['info'].append(line.strip())
 devices=[d for d in devices if any('Video Capture' in x for x in d['info'])]
 return {'devices':devices,'gst':gst,'message':f'发现 {len(devices)} 个采集节点；节点存在不代表已连接图像传感器。'}

def stream_command(device,width,height,fps,mode):
 if not re.fullmatch(r'/dev/video[0-9]+',str(device)): raise UserError('无效视频设备。')
 if (width,height) not in ((320,240),(640,480),(1280,720),(1920,1080)) or fps not in (5,10,15,30) or mode not in ('mjpeg','raw','test'): raise UserError('无效预览参数。')
 caps=f'width={width},height={height},framerate={fps}/1'
 if mode=='test': source=f'videotestsrc is-live=true ! video/x-raw,{caps} ! videoconvert ! jpegenc quality=75'
 elif mode=='mjpeg': source=f'v4l2src device={device} ! image/jpeg,{caps}'
 else: source=f'v4l2src device={device} ! video/x-raw,{caps} ! videoconvert ! jpegenc quality=75'
 pipeline='gst-launch-1.0 -q '+source+' ! queue max-size-buffers=2 leaky=downstream ! fdsink fd=1 sync=false'
 # stdin heartbeats bound remote lifetime even when ADB disappears.
 return "sh "+TARGET+" "+shlex.quote("exec "+pipeline)

class JpegFrames:
 def __init__(self): self.buffer=bytearray()
 def feed(self,data):
  self.buffer.extend(data); newest=None
  while True:
   start=self.buffer.find(b'\xff\xd8')
   if start<0: self.buffer[:]=self.buffer[-1:]; break
   if start: del self.buffer[:start]
   end=self.buffer.find(b'\xff\xd9',2)
   if end<0: break
   newest=bytes(self.buffer[:end+2]); del self.buffer[:end+2]
  if len(self.buffer)>8*1024*1024: self.buffer.clear()
  return newest

def capture(*args): raise UserError('请开始预览后点击保存照片。')


def status(adb,serial):
 out,_,_=adb.shell(serial,f'if [ -f {TARGET} ]; then sha256sum {TARGET}; else echo missing; fi')
 value=out.decode().strip().split()[0]
 digest=hashlib.sha256((ASSETS/'camera-stream.sh').read_bytes()).hexdigest()
 return {'state':'missing' if value=='missing' else 'installed' if value==digest else 'update'}

def install(adb,serial):
 source=ASSETS/'camera-stream.sh'; temp='/tmp/tspi-camera-'+secrets.token_hex(12)+'.sh'
 try:
  adb.run(['-s',serial,'push',str(source),temp])
  digest=hashlib.sha256(source.read_bytes()).hexdigest()
  adb.shell(serial,f'''set -e
command -v gst-launch-1.0 >/dev/null
command -v v4l2-ctl >/dev/null
test "$(sha256sum {temp} | cut -d ' ' -f 1)" = {digest}
sh -n {temp}
mkdir -p /userdata/bin
if [ -f {TARGET} ]; then cp -p {TARGET} {TARGET}.bak; fi
cp {temp} {TARGET}.new
chmod 700 {TARGET}.new
mv {TARGET}.new {TARGET}
sync
''',timeout=25)
 finally: adb.shell(serial,'rm -f '+temp,check=False)
 return status(adb,serial)

def uninstall(adb,serial):
 adb.shell(serial,f'rm -f {TARGET} {TARGET}.bak {TARGET}.new')
 return {'state':'missing'}

def prepare(adb,serial,device,width,height,fps,mode):
 if status(adb,serial)['state']!='installed': raise UserError('请先安装或升级摄像头助手插件。')
 return stream_command(device,width,height,fps,mode)
