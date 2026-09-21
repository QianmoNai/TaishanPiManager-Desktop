from pathlib import Path
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QPlainTextEdit,QLabel,QFileDialog
from PySide6.QtCore import QTimer,QProcess,Qt
from PySide6.QtGui import QImage,QPixmap
from selection_widgets import QComboBox
from camera_plugin import stream_command,JpegFrames

class CameraPanel(QWidget):
 def __init__(self,owner,label,button,card):
  super().__init__(); self.owner=owner; self.devices=[]; self.epoch=0; self.image=QImage(); self.parser=JpegFrames(); self.frames=0
  self.process=QProcess(self); self.process.readyReadStandardOutput.connect(self.receive); self.process.readyReadStandardError.connect(self.errors); self.process.finished.connect(self.finished); self.process.errorOccurred.connect(lambda _:self.status.setText('无法启动预览，请检查 ADB。'))
  self.ping=QTimer(self); self.ping.setInterval(2000); self.ping.timeout.connect(lambda:self.process.write(b'ping\n'))
  self.timeout=QTimer(self); self.timeout.setSingleShot(True); self.timeout.setInterval(10000); self.timeout.timeout.connect(self.no_frames)
  self.kill=QTimer(self); self.kill.setSingleShot(True); self.kill.setInterval(2500); self.kill.timeout.connect(self.process.kill)
  self.paint=QTimer(self); self.paint.setInterval(66); self.paint.timeout.connect(self.show_frame); self.paint.start()
  lay=QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.addWidget(button('‹ 返回插件中心',owner.close_plugin)); f,b=card(); b.addWidget(label('摄像头助手（内测版）','section'))
  self.device=QComboBox(); row=QHBoxLayout(); row.addWidget(self.device,1); self.refresh_btn=button('刷新设备',self.refresh); row.addWidget(self.refresh_btn); b.addLayout(row)
  row=QHBoxLayout(); self.mode=QComboBox()
  for title,value in [('MJPEG 摄像头','mjpeg'),('原始视频转 JPEG','raw'),('测试画面（无需摄像头）','test')]: self.mode.addItem(title,value)
  self.size=QComboBox()
  for w,h in [(320,240),(640,480),(1280,720),(1920,1080)]: self.size.addItem(f'{w}×{h}',(w,h))
  self.size.setCurrentIndex(1); self.fps=QComboBox()
  for fps in (5,10,15,30): self.fps.addItem(f'{fps} fps',fps)
  self.fps.setCurrentIndex(2)
  for x in (self.mode,self.size,self.fps): row.addWidget(x)
  b.addLayout(row); row=QHBoxLayout(); self.start_btn=button('开始预览',self.start,'primary'); row.addWidget(self.start_btn); row.addWidget(button('停止预览',self.stop)); row.addWidget(button('保存照片',self.save)); b.addLayout(row)
  self.status=label('请选择设备，或选择测试画面验证传输。','caption',True); b.addWidget(self.status)
  self.view=QLabel('等待画面'); self.view.setAlignment(Qt.AlignmentFlag.AlignCenter); self.view.setMinimumHeight(300); self.view.setMinimumWidth(0); b.addWidget(self.view)
  self.info=QPlainTextEdit(); self.info.setReadOnly(True); self.info.setMaximumHeight(150); b.addWidget(self.info); b.addWidget(label('通过 ADB 传输，无需开放网络端口。摄像头必须支持所选格式、分辨率和帧率；保存照片为电脑本地 JPEG。','caption',True)); lay.addWidget(f)
 def active(self): return self.process.state()!=QProcess.ProcessState.NotRunning
 def refresh(self):
  if self.active() or self.owner.busy or not self.owner.require_device():return
  epoch=self.epoch; serial=self.owner.serial
  self.owner.work(lambda:self.owner.api.dispatch('/api/camera-inventory',{'serial':serial}),lambda data:self.render(data) if epoch==self.epoch else None,'正在检测摄像头…')
 def render(self,data):
  self.devices=data.get('devices',[]); self.device.clear(); self.device.addItems([d['path'] for d in self.devices]); self.status.setText(data.get('message','')); self.info.setPlainText('\n\n'.join(d['path']+'\n'+'\n'.join(d['info']) for d in self.devices) or '未发现采集节点；可使用测试画面。')
 def start(self):
  if self.active() or self.owner.busy or not self.owner.require_device():return
  try: command=stream_command(self.device.currentText() or ('/dev/video0' if self.mode.currentData()=='test' else ''),*self.size.currentData(),self.fps.currentData(),self.mode.currentData())
  except Exception as exc: self.status.setText(str(exc));return
  self.image=QImage(); self.parser=JpegFrames(); self.frames=0; self.view.clear(); self.status.setText('正在等待画面…')
  self.process.setProgram(self.owner.api.adb.path); self.process.setArguments(['-s',self.owner.serial,'exec-out',command]); self.process.start(); self.ping.start(); self.timeout.start()
  for x in (self.device,self.mode,self.size,self.fps,self.refresh_btn,self.start_btn):x.setEnabled(False)
 def receive(self):
  raw=self.parser.feed(bytes(self.process.readAllStandardOutput()))
  if raw:
   image=QImage.fromData(raw,'JPEG')
   if not image.isNull():self.image=image;self.frames+=1;self.timeout.start();self.status.setText(f'预览中 · {image.width()}×{image.height()} · 已显示 {self.frames} 帧')
 def show_frame(self):
  if not self.image.isNull():self.view.setPixmap(QPixmap.fromImage(self.image).scaled(max(1,self.view.width()),300,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
 def errors(self):
  text=bytes(self.process.readAllStandardError()).decode('utf-8','replace').strip()
  if text:self.status.setText(text[-500:])
 def no_frames(self):self.stop();self.status.setText('未收到有效画面：请检查摄像头连接、占用及格式/分辨率/帧率。')
 def stop(self):
  self.ping.stop();self.timeout.stop()
  if self.active():self.process.write(b'stop\n');self.process.closeWriteChannel();self.kill.start()
 def finished(self,*args):
  self.ping.stop();self.timeout.stop();self.kill.stop()
  for x in (self.device,self.mode,self.size,self.fps,self.refresh_btn,self.start_btn):x.setEnabled(True)
  if self.status.text().startswith(('预览中','正在等待')):self.status.setText('预览已停止')
 def save(self):
  if self.image.isNull():self.status.setText('请先开始预览并等待有效画面。');return
  path,_=QFileDialog.getSaveFileName(self,'保存照片','camera-photo.jpg','JPEG (*.jpg)')
  if path:self.status.setText('照片已保存：'+path if self.image.save(path,'JPEG',95) else '照片保存失败')
 def reset(self):self.epoch+=1;self.stop();self.image=QImage();self.view.clear();self.device.clear();self.info.clear();self.devices=[]
 def hideEvent(self,event):self.stop();super().hideEvent(event)
 def shutdown(self):
  self.stop()
  if self.active() and not self.process.waitForFinished(3000):self.process.kill();self.process.waitForFinished(500)
  self.paint.stop()
