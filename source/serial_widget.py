"""UART assistant with ADB-only transport and bounded receive/plot buffers."""
import codecs,csv,json,time,re
from collections import deque
from PySide6.QtCore import Qt,QProcess,QTimer,QRectF
from PySide6.QtGui import QColor,QPainter,QPen,QPainterPath,QTextCursor
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QPlainTextEdit,QLineEdit,QCheckBox,QSpinBox,QFileDialog,QApplication,QSizePolicy
from selection_widgets import QComboBox
from serial_assistant import inventory,prepare,pin_config,encode_send,WaveDecoder,BAUDS

class WavePlot(QWidget):
    COLORS=['#4aa3ff','#42c890','#e8b54d','#ce85ff','#f07587','#54cddd','#c9cf72','#bc967a']
    def __init__(self):
        super().__init__(); self.points=deque(maxlen=1000); self.visible_channels=list(range(8)); self.setMinimumHeight(230)
    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark=QApplication.instance().property('theme')=='dark'; p.fillRect(self.rect(),QColor('#1c1c20' if dark else '#f7f9fc'))
        area=QRectF(65,35,max(10,self.width()-82),max(10,self.height()-65)); p.setPen(QColor('#8793a5'))
        if not self.points: p.drawText(area,Qt.AlignmentFlag.AlignCenter,'选择 FireWater / JustFloat 协议后显示最多 8 通道波形'); return
        values=[v for row in self.points for v in row]; lo=min(values); hi=max(values)
        if hi-lo<1e-9: lo-=1; hi+=1
        span=max(1e-9,hi-lo)
        for k in range(3):
            y=area.top()+area.height()*k/2; p.drawLine(int(area.left()),int(y),int(area.right()),int(y)); p.drawText(QRectF(0,y-8,60,20),Qt.AlignmentFlag.AlignRight,f'{hi-span*k/2:.3g}')
        for channel,color in enumerate(self.COLORS):
            if channel not in self.visible_channels: continue
            path=QPainterPath(); started=False
            for i,row in enumerate(self.points):
                if len(row)<=channel: started=False; continue
                x=area.left()+i*area.width()/max(1,len(self.points)-1); y=area.bottom()-(row[channel]-lo)/span*area.height()
                if not started: path.moveTo(x,y); started=True
                else: path.lineTo(x,y)
            p.setPen(QPen(QColor(color),1.5)); p.drawPath(path)
            if len(self.points[-1])>channel: p.drawText(10+channel*80,22,f'CH{channel+1}')
        p.setPen(QColor('#8793a5')); p.drawText(65,self.height()-8,f'最近 {len(self.points)} 帧 · 横轴为样本序号 · 纵轴自动缩放')

class SerialPanel(QWidget):
    def __init__(self,owner,label,button,card):
        super().__init__(); self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred); self.setMinimumWidth(0); self.owner=owner; self.generation=0; self.connected=False; self.session_epoch=0; self.preparing=False; self.closing=False; self.rx=0; self.tx=0; self.pending=bytearray(); self.log_pending=bytearray(); self.info={}; self.decoder=WaveDecoder(); self.text_decoder=codecs.getincrementaldecoder('utf-8')('replace')
        self.process=QProcess(self); self.process.readyReadStandardOutput.connect(self.drain); self.process.readyReadStandardError.connect(self.drain_error); self.process.finished.connect(self.finished); self.process.errorOccurred.connect(self.process_error)
        self.heartbeat=QTimer(self); self.heartbeat.setInterval(2000); self.heartbeat.timeout.connect(lambda:self.command({'cmd':'ping'}))
        self.timeout=QTimer(self); self.timeout.setSingleShot(True); self.timeout.setInterval(15000); self.timeout.timeout.connect(self.open_timeout)
        self.close_timeout=QTimer(self); self.close_timeout.setSingleShot(True); self.close_timeout.setInterval(2500); self.close_timeout.timeout.connect(self.process.kill)
        self.repeat=QTimer(self); self.repeat.timeout.connect(self.send)
        self.display=QTimer(self); self.display.setInterval(100); self.display.timeout.connect(self.flush_display); self.display.start()
        box=QVBoxLayout(self); box.setContentsMargins(0,0,0,0); box.setSpacing(14)
        row=QHBoxLayout(); row.addWidget(button('‹ 返回插件中心',owner.close_plugin)); row.addStretch(); self.state=label('串口未打开','subtle'); row.addWidget(self.state); box.addLayout(row)
        frame,inner=card(); inner.addWidget(label('串口助手','section')); inner.addWidget(label('UART3 排针：8 → TX（GPIO3_B7），10 → RX（GPIO3_C0），6 → GND。TX 接对端 RX，RX 接对端 TX，使用 3.3 V TTL 电平并共地。','subtle',True))
        row=QGridLayout(); self.port=QComboBox(); self.port.setMinimumWidth(0); self.port.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred); row.addWidget(self.port,0,0,1,2); self.refresh_btn=button('刷新串口',self.refresh); row.addWidget(self.refresh_btn,0,2); self.open_btn=button('打开串口',self.open_port,'primary'); row.addWidget(self.open_btn,1,0); row.addWidget(button('关闭串口',self.stop),1,1); row.setColumnStretch(0,1); row.setColumnStretch(1,1); inner.addLayout(row)
        grid=QGridLayout(); self.baud=QComboBox()
        for b in BAUDS: self.baud.addItem(str(b),b)
        self.baud.setCurrentText('115200'); self.bits=QComboBox(); self.bits.addItems(['5','6','7','8']); self.bits.setCurrentText('8')
        self.parity=QComboBox()
        for title,value in [('无校验','none'),('偶校验','even'),('奇校验','odd')]: self.parity.addItem(title,value)
        self.stops=QComboBox(); self.stops.addItems(['1','2']); self.flow=QComboBox()
        for title,value in [('无流控','none'),('RTS/CTS','rtscts'),('XON/XOFF','xonxoff')]: self.flow.addItem(title,value)
        for i,(title,widget) in enumerate([('波特率',self.baud),('数据位',self.bits),('校验',self.parity),('停止位',self.stops),('流控',self.flow)]):
            widget.setMinimumWidth(0); widget.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred); col=i%3; row_index=(i//3)*2; grid.addWidget(label(title,'caption'),row_index,col); grid.addWidget(widget,row_index+1,col)
        for col in range(3): grid.setColumnStretch(col,1)
        inner.addLayout(grid); self.port_hint=label('刷新后显示占用状态。UART3 的 8/10 脚连接不提供 RTS/CTS，请使用无流控。','caption',True); inner.addWidget(self.port_hint); self.port.currentIndexChanged.connect(self.port_changed); box.addWidget(frame)
        frame,inner=card(); inner.addWidget(label('数据接收','section')); row=QGridLayout(); self.view=QComboBox(); self.view.addItems(['UTF-8 文本','HEX']); row.addWidget(self.view,0,0); self.pause=QCheckBox('暂停显示'); row.addWidget(self.pause,0,1); self.follow=QCheckBox('自动滚动'); self.follow.setChecked(True); row.addWidget(self.follow,0,2); row.addWidget(button('清空',self.clear),1,0); row.addWidget(button('保存日志',self.save_log),1,1); row.setColumnStretch(2,1); inner.addLayout(row)
        self.receive=QPlainTextEdit(); self.receive.setReadOnly(True); self.receive.setMaximumBlockCount(4000); self.receive.setMinimumHeight(220); inner.addWidget(self.receive); self.count=label('RX 0 B · TX 0 B','caption'); inner.addWidget(self.count); box.addWidget(frame)
        frame,inner=card(); inner.addWidget(label('发送数据','section')); self.input=QPlainTextEdit(); self.input.setPlaceholderText('输入 UTF-8 文本，或勾选 HEX 后输入 01 02 FF'); self.input.setMaximumHeight(100); inner.addWidget(self.input)
        row=QGridLayout(); self.hex=QCheckBox('HEX'); row.addWidget(self.hex,0,0); self.newline=QComboBox(); row.addWidget(self.newline,0,1)
        for title,value in [('不追加换行','none'),('LF','lf'),('CRLF','crlf'),('CR','cr')]: self.newline.addItem(title,value)
        self.periodic=QCheckBox('定时发送'); self.periodic.toggled.connect(self.toggle_repeat); row.addWidget(self.periodic,0,2); self.interval=QSpinBox(); self.interval.setRange(100,60000); self.interval.setValue(1000); self.interval.setSuffix(' ms'); row.addWidget(self.interval,0,3); row.addWidget(button('发送',self.send,'primary'),0,4); row.setColumnStretch(4,0); inner.addLayout(row); box.addWidget(frame)
        frame,inner=card(); inner.addWidget(label('实时波形','section')); row=QGridLayout(); row.addWidget(label('显示变量','caption'),0,0); self.channel_checks=[]
        for i in range(8):
            check=QCheckBox(f'CH{i+1}'); check.setChecked(True); check.toggled.connect(lambda checked,ch=i:self.set_channel_visible(ch,checked)); self.channel_checks.append(check); row.addWidget(check,(i//4)+1,i%4)
        self.protocol=QComboBox(); self.protocol.addItems(['不解析波形','FireWater（CSV 文本）','JustFloat（浮点帧）']); self.protocol.currentIndexChanged.connect(self.change_protocol); row.addWidget(self.protocol,3,0,1,3); row.addWidget(button('导出 CSV',self.save_csv),3,3); inner.addLayout(row); self.plot=WavePlot(); inner.addWidget(self.plot); inner.addWidget(label('FireWater：1.0,2.0\\n；JustFloat：小端 float32 + 00 00 80 7F 帧尾。最多 8 通道，保留最近 1000 帧。','caption',True)); box.addWidget(frame)
        frame,inner=card(); inner.addWidget(label('串口引脚复用','section')); row=QGridLayout(); self.uart=QComboBox(); self.uart.currentIndexChanged.connect(self.select_uart); row.addWidget(self.uart,0,0); self.group=QComboBox(); self.group.currentIndexChanged.connect(self.pin_hint); row.addWidget(self.group,0,1); self.enabled=QCheckBox('启用'); self.enabled.setChecked(True); row.addWidget(self.enabled,1,0); row.addWidget(button('导出 SDK 配置',self.export_pins),1,1); row.setColumnStretch(0,1); row.setColumnStretch(1,1); inner.addLayout(row); self.pin_text=label('刷新后读取当前固件的 UART 引脚组。','caption',True); inner.addWidget(self.pin_text); inner.addWidget(label('波特率等参数在打开串口时应用、关闭时恢复。引脚复用由设备树决定：此处生成 .dtsi 配置，由你合入 SDK 并手动编译；不会在线改写 GPIO 或固件。','caption',True)); box.addWidget(frame)
        self.settings=[self.port,self.refresh_btn,self.baud,self.bits,self.parity,self.stops,self.flow,self.open_btn]

    def active(self): return self.preparing or self.process.state()!=QProcess.ProcessState.NotRunning
    def set_locked(self,value):
        for w in self.settings: w.setEnabled(not value)
    def refresh(self):
        if self.active() or self.owner.busy or not self.owner.require_device(): return
        serial=self.owner.serial; epoch=self.generation
        def done(data):
            if serial!=self.owner.serial or epoch!=self.generation: return
            self.info=data; self.port.clear()
            for p in data['ports']: self.port.addItem(p['path']+(' · 保留' if p['reserved'] else ' · 占用' if p['owners'] else ' · 可用'),p)
            for i in range(self.port.count()):
                if self.port.itemData(i)['path']=='/dev/ttyS3': self.port.setCurrentIndex(i)
            self.uart.clear()
            for u in data['uarts']: self.uart.addItem(f"UART{u['index']} · {u['status']}",u)
            for i in range(self.uart.count()):
                if self.uart.itemData(i)['index']==3: self.uart.setCurrentIndex(i)
            self.port_changed(); self.state.setText('已刷新 · '+data.get('model',''))
        self.owner.work(lambda:inventory(self.owner.api.adb,serial),done,'正在读取串口与引脚信息…')
    def port_changed(self):
        p=self.port.currentData() or {}; reason=p.get('reserved') or ('占用 PID：'+','.join(map(str,p['owners'])) if p.get('owners') else '可打开')
        self.port_hint.setText((p.get('path','未选择串口')+' · '+reason)+'。8/10 脚 UART3 使用无流控；已占用端口不会强制抢占。')
    def select_uart(self):
        self.group.clear(); u=self.uart.currentData() or {}
        for g in u.get('groups',[]): self.group.addItem(g['name']+(' · 当前' if g['active'] else ''),g)
        for i in range(self.group.count()):
            if self.group.itemData(i)['active']: self.group.setCurrentIndex(i)
        self.pin_hint()
    def pin_hint(self):
        g=self.group.currentData() or {}; self.pin_text.setText('组内 GPIO：'+', '.join(g.get('pins',[]))+('；UART3_M1：排针 8 TX / 10 RX。' if g.get('name')=='uart3m1-xfer' else '；其他引脚组须核对原理图及复用冲突。'))
    def export_pins(self):
        try: text=pin_config(self.uart.currentData() or {},self.group.currentData() or {},self.enabled.isChecked())
        except Exception as e: self.state.setText(str(e)); return
        path,_=QFileDialog.getSaveFileName(self,'导出串口设备树片段','taishanpi-uart.dtsi','设备树片段 (*.dtsi)')
        if path:
            try:
                from pathlib import Path
                Path(path).write_text(text,encoding='utf-8'); self.state.setText('已导出配置，请在 SDK 中审查引脚冲突后手动编译。')
            except OSError as e: self.state.setText('保存失败：'+str(e))
    def open_port(self):
        if self.active() or self.owner.busy or not self.owner.require_device(): return
        p=self.port.currentData()
        if not p: self.state.setText('请先刷新并选择串口。'); return
        if p['reserved'] or p['owners']: self.state.setText('串口已保留或被占用，请先释放后刷新。'); return
        if p['path']=='/dev/ttyS3' and self.flow.currentData()=='rtscts': self.state.setText('UART3 的 8/10 脚未引出 RTS/CTS，请选择无流控。'); return
        serial=self.owner.serial; epoch=self.generation; args=(p['path'],self.baud.currentData(),int(self.bits.currentText()),self.parity.currentData(),int(self.stops.currentText()),self.flow.currentData())
        self.preparing=True; self.set_locked(True); self.state.setText('正在准备串口…')
        def done(command):
            self.preparing=False
            if serial!=self.owner.serial or epoch!=self.generation:
                self.set_locked(False)
                target=re.search(r'/tmp/tspi-serial-[a-f0-9]{24}\.pl',command)
                if target: self.owner.work(lambda:self.owner.api.adb.shell(serial,'rm -f '+target.group(),check=False),lambda _:None,'正在清理已取消的串口会话…')
                return
            self.session_epoch=epoch; self.pending.clear(); self.closing=False; self.process.setProgram(self.owner.api.adb.path); self.process.setArguments(['-s',serial,'shell','-T',command]); self.process.start(); self.timeout.start()
        def failed(_): self.preparing=False; self.set_locked(False); self.state.setText('串口打开失败，请查看提示。')
        self.owner.work(lambda:prepare(self.owner.api.adb,serial,*args),done,'正在准备串口助手…'); self.owner.job.signals.error.connect(failed)
    def command(self,data):
        if self.process.state()==QProcess.ProcessState.Running: self.process.write((json.dumps(data)+'\n').encode())
    def drain(self):
        self.pending.extend(bytes(self.process.readAllStandardOutput()))
        if len(self.pending)>262144: self.state.setText('通信缓冲区异常'); self.stop(); self.pending.clear(); return
        while b'\n' in self.pending:
            line,_,rest=self.pending.partition(b'\n'); self.pending=bytearray(rest)
            try: event=json.loads(line)
            except Exception: continue
            if self.session_epoch!=self.generation: continue
            kind=event.get('event')
            if kind=='ready':
                self.timeout.stop()
                if self.closing: self.command({'cmd':'stop'}); continue
                self.connected=True; self.heartbeat.start(); self.state.setText('串口已打开 · 参数在关闭时恢复')
            elif kind=='rx':
                try: raw=bytes.fromhex(event['hex'])
                except Exception: continue
                self.rx+=len(raw)
                if not self.pause.isChecked(): self.log_pending.extend(raw)
                if len(self.log_pending)>65536: del self.log_pending[:-65536]
                self.plot.points.extend(self.decoder.feed(raw))
            elif kind=='tx': self.tx+=int(event.get('count',0))
            elif kind=='error': self.state.setText('串口错误：'+event.get('message','')); self.stop()
    def drain_error(self):
        data=bytes(self.process.readAllStandardError()).decode('utf-8','replace').strip()
        if data: self.state.setText(data[-300:])
    def process_error(self,error):
        if error==QProcess.ProcessError.FailedToStart: self.timeout.stop(); self.set_locked(False); self.preparing=False; self.state.setText('ADB 启动失败。')
    def open_timeout(self): self.state.setText('串口打开超时'); self.stop()
    def stop(self):
        if self.preparing: self.generation+=1; self.state.setText("正在取消打开串口…")
        self.periodic.setChecked(False); self.repeat.stop(); self.heartbeat.stop(); self.timeout.stop(); self.connected=False; self.closing=True
        if self.process.state()!=QProcess.ProcessState.NotRunning: self.command({'cmd':'stop'}); self.close_timeout.start()
    def finished(self,*args):
        self.connected=False; self.closing=False; self.heartbeat.stop(); self.timeout.stop(); self.close_timeout.stop(); self.repeat.stop(); self.periodic.setChecked(False); self.set_locked(False)
        if self.state.text().startswith('串口已打开'): self.state.setText('串口已关闭，参数已恢复')
    def send(self):
        if not self.connected: self.periodic.setChecked(False); self.state.setText('请先打开串口。'); return
        try: raw=encode_send(self.input.toPlainText(),self.hex.isChecked(),self.newline.currentData())
        except Exception as e: self.periodic.setChecked(False); self.state.setText(str(e)); return
        if self.process.bytesToWrite()>65536: self.periodic.setChecked(False); self.state.setText('发送队列繁忙，已停止定时发送。'); return
        self.command({'cmd':'send','hex':raw.hex()})
    def toggle_repeat(self,checked):
        if checked and self.connected: self.repeat.start(self.interval.value())
        else: self.repeat.stop(); self.periodic.setChecked(False)
    def flush_display(self):
        if self.log_pending:
            raw=bytes(self.log_pending); self.log_pending.clear(); text=raw.hex(' ').upper()+'\n' if self.view.currentIndex() else self.text_decoder.decode(raw)
            bar=self.receive.verticalScrollBar(); previous=bar.value(); cursor=self.receive.textCursor(); cursor.movePosition(QTextCursor.MoveOperation.End); cursor.insertText(text)
            if self.receive.document().characterCount()>262144: cursor.movePosition(QTextCursor.MoveOperation.Start); cursor.movePosition(QTextCursor.MoveOperation.NextCharacter,QTextCursor.MoveMode.KeepAnchor,65536); cursor.removeSelectedText()
            if self.follow.isChecked(): bar.setValue(bar.maximum())
            else: bar.setValue(previous)
        self.count.setText(f'RX {self.rx} B · TX {self.tx} B · 无效波形帧 {self.decoder.dropped}'); self.plot.update()
    def clear(self): self.receive.clear(); self.log_pending.clear(); self.rx=0; self.tx=0; self.text_decoder.reset(); self.plot.points.clear(); self.plot.visible_channels=list(range(8)); [c.setChecked(True) for c in self.channel_checks]; self.change_protocol()
    def set_channel_visible(self,channel,visible):
        if visible and channel not in self.plot.visible_channels: self.plot.visible_channels.append(channel); self.plot.visible_channels.sort()
        elif not visible and channel in self.plot.visible_channels: self.plot.visible_channels.remove(channel)
        self.plot.update()
    def change_protocol(self,*args): self.decoder=WaveDecoder(['none','firewater','justfloat'][self.protocol.currentIndex()]); self.plot.points.clear()
    def save_log(self):
        path,_=QFileDialog.getSaveFileName(self,'保存当前接收日志','serial-log.txt','文本 (*.txt)')
        if path:
            try:
                from pathlib import Path
                Path(path).write_text(self.receive.toPlainText(),encoding='utf-8')
            except OSError as e: self.state.setText('保存失败：'+str(e))
    def save_csv(self):
        path,_=QFileDialog.getSaveFileName(self,'保存当前波形','serial-wave.csv','CSV (*.csv)')
        if path:
            try:
                with open(path,'w',encoding='utf-8-sig',newline='') as f:
                    writer=csv.writer(f); writer.writerow(['sample']+[f'CH{i+1}' for i in range(8)])
                    for i,row in enumerate(self.plot.points): writer.writerow([i]+row+['']*(8-len(row)))
            except OSError as e: self.state.setText('保存失败：'+str(e))
    def reset(self):
        self.generation+=1; self.stop(); self.info={}; self.port.clear(); self.uart.clear(); self.clear(); self.state.setText('串口未打开')
    def shutdown(self):
        self.stop()
        if self.process.state()!=QProcess.ProcessState.NotRunning and not self.process.waitForFinished(2000): self.process.kill(); self.process.waitForFinished(500)
        self.display.stop()
