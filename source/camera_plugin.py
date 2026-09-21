from adb_core import UserError
import re
def inventory(adb,serial):
 out,_,_=adb.shell(serial,"for d in /dev/video*; do [ -e \"$d\" ] || continue; echo DEVICE:$d; v4l2-ctl --device=\"$d\" --all 2>/dev/null | head -35; done",timeout=15,check=False)
 devices=[]; cur=None
 for line in out.decode("utf-8","replace").splitlines():
  if line.startswith("DEVICE:"): cur={"path":line[7:],"info":[]}; devices.append(cur)
  elif cur and line.strip(): cur["info"].append(line.strip())
 return {"devices":devices,"message":"未发现 /dev/video*" if not devices else f"发现 {len(devices)} 个视频设备"}
def capture(adb,serial,device):
 if not re.fullmatch(r"/dev/video[0-9]+",str(device)): raise UserError("视频设备路径无效。")
 out,_,code=adb.shell(serial,f"command -v v4l2-ctl >/dev/null || exit 7; v4l2-ctl -d {device} --stream-mmap --stream-count=1 --stream-to=/userdata/camera-capture.raw",timeout=20,check=False)
 if code: raise UserError("抓拍失败，请检查摄像头驱动和格式支持。")
 return {"path":"/userdata/camera-capture.raw","message":"已抓拍到泰山派 /userdata/camera-capture.raw"}
