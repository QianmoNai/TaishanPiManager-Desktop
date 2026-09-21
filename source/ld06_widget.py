import csv
import json
import math
import time
from PySide6.QtCore import Qt, QProcess, QTimer, QPointF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QSpinBox
from selection_widgets import QComboBox
import ld06_plugin as plugin


class RadarPlot(QWidget):
    def __init__(self):
        super().__init__(); self.setMinimumHeight(360); self.points={}; self.range=3; self.confidence=0
    def paintEvent(self, event):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(),QColor('#121d2a'))
        center=QPointF(self.width()/2,self.height()/2); radius=max(10,min(self.width(),self.height())/2-32)
        p.setPen(QPen(QColor('#32475c'),1))
        for i in range(1,5):
            r=radius*i/4; p.drawEllipse(center,r,r); p.drawText(int(center.x()+5),int(center.y()-r+14),f'{self.range*i/4:g} m')
        p.drawLine(QPointF(center.x()-radius,center.y()),QPointF(center.x()+radius,center.y()))
        p.drawLine(QPointF(center.x(),center.y()-radius),QPointF(center.x(),center.y()+radius))
        p.setPen(QColor('#a7b6c7')); p.drawText(int(center.x()-12),18,'0°'); p.drawText(self.width()-35,int(center.y()),'90°')
        p.setPen(QPen(QColor('#42dbb0'),3))
        now=time.monotonic()
        for angle,distance,confidence,stamp in self.points.values():
            if now-stamp>1.5 or distance>self.range or confidence<self.confidence: continue
            theta=math.radians(angle); r=distance/self.range*radius
            p.drawPoint(QPointF(center.x()+math.sin(theta)*r,center.y()-math.cos(theta)*r))
        if not self.points:
            p.setPen(QColor('#a7b6c7')); p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'等待 LD06 数据 · 角度顺时针增加')


class RadarPanel(QWidget):
    def __init__(self,owner,label,button,card):
        super().__init__(); self.owner=owner; self.epoch=0; self.preparing=False; self.pending=bytearray(); self.decoder=plugin.Decoder(); self.last_rx=0; self.session_epoch=0
        self.process=QProcess(self); self.process.readyReadStandardOutput.connect(self.drain)
        self.process.readyReadStandardError.connect(self.error_output); self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(lambda _:self.state.setText('采集进程异常，请检查 ADB 连接。'))
        self.heartbeat=QTimer(self); self.heartbeat.setInterval(2000); self.heartbeat.timeout.connect(lambda:self.command('ping'))
        self.deadline=QTimer(self); self.deadline.setSingleShot(True); self.deadline.setInterval(15000); self.deadline.timeout.connect(self.open_timeout)
        self.kill_timer=QTimer(self); self.kill_timer.setSingleShot(True); self.kill_timer.setInterval(2500); self.kill_timer.timeout.connect(self.process.kill)
        self.repaint_timer=QTimer(self); self.repaint_timer.setInterval(100); self.repaint_timer.timeout.connect(self.display); self.repaint_timer.start()
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        layout.addWidget(button('‹ 返回插件中心',owner.close_plugin))
        frame,box=card(); box.addWidget(label('LD06 雷达视图','section'))
        box.addWidget(label('可选插件 · UART3 /dev/ttyS3 · 230400 · 8N1','subtle',True))
        self.install_state=label('尚未检测安装状态','caption'); box.addWidget(self.install_state)
        row=QHBoxLayout(); row.addWidget(button('下载插件包',self.download)); self.install_btn=button('安装 / 升级到泰山派',lambda:self.action('install'),'primary'); row.addWidget(self.install_btn)
        self.status_btn=button('刷新状态',lambda:self.action('status')); row.addWidget(self.status_btn); self.remove_btn=button('卸载',lambda:self.action('uninstall')); row.addWidget(self.remove_btn); box.addLayout(row)
        box.addWidget(label('下载保存离线 ZIP 插件包；安装使用随软件提供的校验文件，不需要联网或编译 SDK。','caption',True)); layout.addWidget(frame)
        frame,box=card(); box.addWidget(label('实时扫描','section')); row=QHBoxLayout()
        self.start_btn=button('开始采集',self.start,'primary'); row.addWidget(self.start_btn); row.addWidget(button('停止采集',self.stop))
        row.addWidget(button('导出当前点云',self.export)); box.addLayout(row)
        row=QHBoxLayout(); row.addWidget(label('显示半径','caption')); self.range=QComboBox()
        for value in (1,2,3,6,12): self.range.addItem(f'{value} m',value)
        self.range.setCurrentIndex(2); row.addWidget(self.range); row.addWidget(label('最低信号强度','caption')); self.confidence=QSpinBox(); self.confidence.setRange(0,255); row.addWidget(self.confidence); row.addStretch(); box.addLayout(row)
        self.state=label('未开始采集','subtle',True); box.addWidget(self.state); self.plot=RadarPlot(); box.addWidget(self.plot)
        self.stats=label('有效帧 0 · CRC 错误 0','caption'); box.addWidget(self.stats)
        box.addWidget(label('接线：LD06 5V → 排针 4，GND → 排针 6，TX → 排针 10（UART3 RX），PWM → GND。仅接收雷达数据。串口被其他程序占用时，请先释放；结束后恢复原监控。','caption',True)); layout.addWidget(frame)
    def controls(self): return [self.install_btn,self.status_btn,self.remove_btn,self.start_btn]
    def active(self): return self.preparing or self.process.state()!=QProcess.ProcessState.NotRunning
    def action(self,action):
        if self.owner.busy or not self.owner.require_device(): return
        if self.active(): self.state.setText('请先停止采集，再管理插件。'); return
        serial=self.owner.serial; epoch=self.epoch
        def done(result):
            if epoch!=self.epoch or serial!=self.owner.serial: return
            self.install_state.setText({'missing':'未安装','installed':'已安装 · v1.0','update':'可升级'}[result['state']])
            self.owner.plugin_center.render_ld06(result)
        self.owner.work(lambda:getattr(plugin,action)(self.owner.api.adb,serial),done,'正在处理 LD06 插件…')
    def download(self):
        path,_=QFileDialog.getSaveFileName(self,'下载离线插件包','ld06-radar-v1.0.zip','ZIP (*.zip)')
        if path:
            try: plugin.export_package(path); self.owner.notify('插件包已保存：'+path)
            except OSError as exc: self.owner.notify('保存失败：'+str(exc),True)
    def start(self):
        if self.active() or self.owner.busy or not self.owner.require_device(): return
        serial=self.owner.serial; epoch=self.epoch; self.preparing=True
        self.state.setText('正在检查安装状态与 UART3 占用…')
        def done(command):
            self.preparing=False
            if epoch!=self.epoch or serial!=self.owner.serial: return
            self.decoder=plugin.Decoder(); self.plot.points.clear(); self.pending.clear(); self.last_rx=0; self.session_epoch=epoch
            self.process.setProgram(self.owner.api.adb.path); self.process.setArguments(['-s',serial,'shell','-T',command]); self.process.start(); self.deadline.start()
        def failed(_): self.preparing=False; self.state.setText('无法开始采集，请查看上方错误提示。')
        self.owner.work(lambda:plugin.prepare(self.owner.api.adb,serial),done,'正在准备 LD06…'); self.owner.job.signals.error.connect(failed)
    def command(self,cmd):
        if self.process.state()==QProcess.ProcessState.Running: self.process.write((json.dumps({'cmd':cmd})+'\n').encode())
    def drain(self):
        self.pending.extend(bytes(self.process.readAllStandardOutput()))
        if len(self.pending)>262144: self.stop(); self.state.setText('通信缓冲区异常，已停止。'); self.pending.clear(); return
        while b'\n' in self.pending:
            line,_,rest=self.pending.partition(b'\n'); self.pending=bytearray(rest)
            if self.session_epoch!=self.epoch: continue
            try: event=json.loads(line)
            except ValueError: continue
            if event.get('event')=='ready':
                self.deadline.stop(); self.heartbeat.start(); self.last_rx=time.monotonic(); self.state.setText('正在接收 LD06 数据…')
            elif event.get('event')=='rx':
                try: points=self.decoder.feed(bytes.fromhex(event['hex']))
                except (ValueError,KeyError): continue
                if points: self.last_rx=time.monotonic(); self.state.setText('正在采集 · UART3 / 230400')
                for angle,distance,confidence in points: self.plot.points[int(angle*2)%720]=(angle,distance,confidence,time.monotonic())
            elif event.get('event')=='error': self.stop(); self.state.setText('采集失败：'+event.get('message',''))
    def display(self):
        now=time.monotonic(); self.plot.points={k:v for k,v in self.plot.points.items() if now-v[3]<=1.5}
        self.plot.range=self.range.currentData(); self.plot.confidence=self.confidence.value(); self.plot.update()
        self.stats.setText(f'有效帧 {self.decoder.frames} · CRC 错误 {self.decoder.bad} · 转速 {self.decoder.speed:.1f} Hz · 当前点数 {len(self.plot.points)}')
        if self.heartbeat.isActive() and now-self.last_rx>3: self.state.setText('未收到有效 LD06 帧，请检查供电、接线和串口参数。')
    def stop(self):
        self.epoch+=1; self.heartbeat.stop(); self.deadline.stop()
        if self.process.state()!=QProcess.ProcessState.NotRunning: self.command('stop'); self.kill_timer.start()
        self.state.setText('采集已停止')
    def open_timeout(self): self.stop(); self.state.setText('打开串口超时，请检查 ADB。')
    def error_output(self):
        text=bytes(self.process.readAllStandardError()).decode('utf-8','replace').strip()
        if text: self.state.setText(text[-300:])
    def finished(self,*args):
        self.heartbeat.stop(); self.deadline.stop(); self.kill_timer.stop()
        if self.state.text().startswith(('正在','未收到')): self.state.setText('采集连接已结束，串口参数已恢复。')
    def export(self):
        path,_=QFileDialog.getSaveFileName(self,'导出当前点云','ld06-points.csv','CSV (*.csv)')
        if path:
            try:
                with open(path,'w',newline='',encoding='utf-8-sig') as f:
                    writer=csv.writer(f); writer.writerow(['angle_deg','distance_m','confidence'])
                    writer.writerows(v[:3] for v in sorted(self.plot.points.values()))
            except OSError as exc: self.owner.notify(str(exc),True)
    def reset(self): self.stop(); self.plot.points.clear(); self.decoder=plugin.Decoder(); self.install_state.setText('尚未检测安装状态')
    def shutdown(self):
        self.stop()
        if self.process.state()!=QProcess.ProcessState.NotRunning and not self.process.waitForFinished(2000): self.process.kill(); self.process.waitForFinished(500)
        self.repaint_timer.stop()
