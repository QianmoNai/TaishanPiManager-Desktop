"""V4L2 discovery and bounded JPEG streaming over ADB."""
import re, shlex
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
 return "sh -c "+shlex.quote("""child=; trap '[ -z "$child" ] || kill "$child" 2>/dev/null; wait "$child" 2>/dev/null' EXIT; trap 'exit 0' HUP INT TERM; """+pipeline+""" </dev/null & child=$!; while kill -0 "$child" 2>/dev/null; do read -r -t 6 heartbeat || break; [ "$heartbeat" != stop ] || break; done""")

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
