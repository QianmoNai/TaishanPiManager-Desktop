from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QPlainTextEdit,QComboBox
from PySide6.QtCore import QTimer
from camera_plugin import inventory,capture
class CameraPanel(QWidget):
 def __init__(self,owner,label,button,card):
  super().__init__(); self.owner=owner; self.devices=[]; lay=QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.addWidget(button('‹ 返回插件中心',owner.close_plugin)); f,b=card(); b.addWidget(label('摄像头助手（内测版）','section')); b.addWidget(label('检测泰山派 USB / MIPI 摄像头，查看设备信息并抓拍。','subtle',True)); row=QHBoxLayout(); self.device=QComboBox(); row.addWidget(self.device,1); row.addWidget(button('刷新设备',self.refresh)); row.addWidget(button('抓拍',self.capture,'primary')); b.addLayout(row); self.status=label('未读取摄像头设备','caption'); b.addWidget(self.status); self.info=QPlainTextEdit(); self.info.setReadOnly(True); self.info.setMinimumHeight(280); b.addWidget(self.info); lay.addWidget(f); lay.addStretch(); self.refresh()
 def refresh(self):
  if not self.owner.require_device() or self.owner.busy:return
  self.owner.work(lambda:self.owner.call('camera-inventory'),self.render,'正在检测摄像头…')
 def render(self,data):
  self.devices=data.get('devices',[]); self.device.clear(); self.device.addItems([d['path'] for d in self.devices]); self.status.setText(data.get('message','')); self.info.setPlainText('\n\n'.join(d['path']+'\n'+'\n'.join(d['info']) for d in self.devices) or '未发现视频设备')
 def capture(self):
  if not self.devices:return
  dev=self.device.currentText(); self.owner.work(lambda:self.owner.call('camera-capture',{'device':dev}),lambda d:self.status.setText(d['message']),'正在抓拍…')
 def reset(self): self.devices=[]; self.device.clear(); self.info.clear(); self.status.setText('未读取摄像头设备')
 def active(self): return False
 def shutdown(self): pass
