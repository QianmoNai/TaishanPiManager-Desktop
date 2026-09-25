"""Native VT terminal backed by an ADB PTY. No browser or local shell."""
import codecs
import copy
import re
import secrets
from pathlib import Path

import pyte
from adb_core import adb_arguments
from wcwidth import wcswidth
from PySide6.QtCore import Qt, QProcess, QTimer, Signal, QRect, QPoint
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QMessageBox


class Screen(pyte.HistoryScreen):
    def __init__(self, columns, lines, reply):
        self.alternate=None; self.reply=reply
        super().__init__(columns,lines,history=2000)

    def write_process_input(self, data): self.reply(data.encode('utf-8'))

    def set_mode(self, *modes, **kwargs):
        if kwargs.get('private') and any(m in modes for m in (47,1047,1049)) and self.alternate is None:
            self.alternate={k:copy.deepcopy(getattr(self,k)) for k in ('buffer','cursor','history','savepoints','margins','mode')}
            super().reset()
        super().set_mode(*modes,**kwargs)

    def reset_mode(self, *modes, **kwargs):
        if kwargs.get('private') and any(m in modes for m in (47,1047,1049)) and self.alternate is not None:
            saved=self.alternate; self.alternate=None
            for key,value in saved.items(): setattr(self,key,value)
            self.cursor.x=min(self.cursor.x,self.columns-1); self.cursor.y=min(self.cursor.y,self.lines-1)
            self.dirty.update(range(self.lines))
        super().reset_mode(*modes,**kwargs)


COLORS={'black':'#171a22','red':'#f07178','green':'#a8d88d','brown':'#e5c07b',
        'blue':'#75aaff','magenta':'#c792ea','cyan':'#73daca','white':'#cdd6e2',
        'brightblack':'#737d91','brightred':'#ff939a','brightgreen':'#c3f5a8',
        'brightbrown':'#ffe6a4','brightblue':'#a4c7ff','brightmagenta':'#e1b4ff',
        'brightcyan':'#a7f4ec','brightwhite':'#ffffff'}


class Terminal(QAbstractScrollArea):
    connectionChanged=Signal(bool,str)
    sizeChanged=Signal(int,int)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setObjectName('nativeTerminal'); self.setAccessibleName('泰山派交互终端')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus); self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.terminal_font=QFont('Consolas'); self.terminal_font.setPixelSize(14)
        self.setFont(self.terminal_font); self.cell_width=9; self.cell_height=20
        self.screen=Screen(80,24,self.send); self.stream=pyte.Stream(self.screen)
        self.process=QProcess(self); self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.drain)
        self.process.started.connect(self.started); self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        self.resize_process=QProcess(self); self.resize_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.resize_process.started.connect(self.write_size)
        self.resize_process.readyReadStandardOutput.connect(lambda:self.resize_process.readAllStandardOutput())
        self.resize_timer=QTimer(self); self.resize_timer.setSingleShot(True); self.resize_timer.setInterval(250); self.resize_timer.timeout.connect(self.send_size)
        self.resize_timeout=QTimer(self); self.resize_timeout.setSingleShot(True); self.resize_timeout.setInterval(3000); self.resize_timeout.timeout.connect(self.resize_process.kill)
        self.resize_process.finished.connect(lambda *args:self.resize_timeout.stop())
        self.connected=False; self.serial=''; self.adb=''; self.tty=''; self.token=''; self.pending=''
        self.decoder=codecs.getincrementaldecoder('utf-8')('replace')
        self.selection=None; self.anchor=None
        self.viewport().setMouseTracking(True)
        self.verticalScrollBar().valueChanged.connect(lambda:self.viewport().update())
        self.repaint_timer=QTimer(self); self.repaint_timer.setInterval(33); self.repaint_timer.timeout.connect(self.refresh_display)
        self.changed=True; self.repaint_timer.start()
        self.feed('泰山派交互终端\r\n选择设备后会自动建立会话。\r\n')

    def is_active(self):
        return self.connected or self.process.state()!=QProcess.ProcessState.NotRunning

    def connect_device(self,adb,serial):
        if self.process.state()!=QProcess.ProcessState.NotRunning: return
        if not serial or not Path(adb).is_file():
            self.connectionChanged.emit(False,'请先选择在线设备，并检查 ADB。'); return
        self.adb=adb; self.serial=serial; self.tty=''; self.token=secrets.token_hex(12); self.pending=''
        self.decoder.reset(); self.clear_screen()
        self.process.setProgram(adb); self.process.setArguments(adb_arguments(['-s',serial,'shell','-tt']))
        self.connectionChanged.emit(False,'正在建立终端会话…'); self.process.start()

    def started(self):
        self.connected=True
        # Session-only environment and PTY dimensions. No rc/config file is changed.
        cmd=("export TERM=xterm-256color; "
             "if [ -n \"$BASH_VERSION\" ]; then bind 'set input-meta on'; bind 'set output-meta on'; bind 'set convert-meta off'; bind 'set enable-bracketed-paste on'; fi; "
             "stty rows %d cols %d; "
             "printf '\\033[2J\\033[H\\033]777;%s;%%s\\007' \"$(tty)\"\n")%(self.screen.lines,self.screen.columns,self.token)
        self.send(cmd.encode('ascii'))
        self.connectionChanged.emit(True,'已连接 · '+self.serial); self.setFocus()

    def process_error(self,error):
        if error==QProcess.ProcessError.FailedToStart:
            self.connected=False; self.connectionChanged.emit(False,'ADB 启动失败：'+self.process.errorString())

    def finished(self,code,status):
        self.drain(); self.connected=False; self.tty=''; self.pending=''
        self.resize_timer.stop(); self.resize_process.kill()
        self.feed('\r\n\x1b[0m[终端会话已结束，可重新连接]\r\n')
        self.connectionChanged.emit(False,'会话已结束 · 可重新连接')

    def disconnect_device(self):
        self.resize_timer.stop(); self.resize_timeout.stop()
        if self.resize_process.state()!=QProcess.ProcessState.NotRunning:
            self.resize_process.kill(); self.resize_process.waitForFinished(1000)
        if self.process.state()!=QProcess.ProcessState.NotRunning:
            self.process.kill(); self.process.waitForFinished(1500)
        self.connected=False; self.tty=''

    def send(self,data):
        if self.connected and self.process.state()==QProcess.ProcessState.Running:
            self.process.write(data); self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def drain(self):
        data=bytes(self.process.read(65536))
        if data: self.consume(self.decoder.decode(data))
        if self.process.bytesAvailable(): QTimer.singleShot(0,self.drain)

    def consume(self,text):
        text=self.pending+text; self.pending=''
        prefix='\x1b]777;'+self.token+';'
        match=re.search(re.escape(prefix)+r'(/dev/pts/[0-9]{1,6})\x07',text)
        if match:
            self.tty=match[1]; text=text[:match.start()]+text[match.end():]; self.resize_timer.start()
        # Keep an incomplete OSC marker until the next UTF-8 chunk arrives.
        pos=text.rfind('\x1b]777;')
        if pos>=0 and '\x07' not in text[pos:] and len(text)-pos<160:
            self.pending=text[pos:]; text=text[:pos]
        else:
            pos=text.rfind('\x1b')
            if pos>=0 and prefix.startswith(text[pos:]): self.pending=text[pos:]; text=text[:pos]
        self.feed(text)

    def feed(self,text):
        if not text: return
        self.stream.feed(text); self.changed=True

    def clear_screen(self):
        self.screen.alternate=None; self.screen.reset(); self.selection=None; self.anchor=None
        self.refresh_display(force=True)

    def refresh_display(self,force=False):
        if not self.changed and not force: return
        bar=self.verticalScrollBar(); follow=bar.value()>=bar.maximum()
        history=len(self.screen.history.top)
        bar.setRange(0,history); bar.setPageStep(self.screen.lines)
        if follow: bar.setValue(history)
        self.changed=False; self.viewport().update()

    def rows(self):
        return list(self.screen.history.top)+[self.screen.buffer[y] for y in range(self.screen.lines)]

    def color(self,value,default):
        if value=='default': return QColor(default)
        if value in COLORS: return QColor(COLORS[value])
        if re.fullmatch('[0-9a-fA-F]{6}',value): return QColor('#'+value)
        return QColor(default)

    def paintEvent(self,event):
        p=QPainter(self.viewport()); p.fillRect(self.viewport().rect(),QColor('#11151e'))
        metrics=QFontMetrics(self.terminal_font); baseline=metrics.ascent()
        rows=self.rows(); offset=self.verticalScrollBar().value()
        selection=sorted(self.selection) if self.selection else None
        for y,line in enumerate(rows[offset:offset+self.screen.lines]):
            for x in range(self.screen.columns):
                cell=line[x]
                if cell.data=='': continue
                width=max(1,wcswidth(cell.data))
                fg=self.color(cell.fg,'#d6deeb'); bg=self.color(cell.bg,'#11151e')
                if cell.reverse: fg,bg=bg,fg
                if selection and selection[0]<=(offset+y,x)<selection[1]: bg=QColor('#314e78')
                rect=QRect(10+x*self.cell_width,8+y*self.cell_height,width*self.cell_width,self.cell_height)
                p.fillRect(rect,bg)
                if cell.data:
                    font=QFont(self.terminal_font); font.setBold(cell.bold); font.setItalic(cell.italics); font.setUnderline(cell.underscore)
                    p.setFont(font); p.setPen(fg); p.drawText(rect.x(),rect.y()+baseline,cell.data)
        if self.connected and not self.screen.cursor.hidden and offset==len(self.screen.history.top):
            x=min(self.screen.cursor.x,self.screen.columns-1); y=self.screen.cursor.y
            p.setPen(QPen(QColor('#82b5ff'),2)); p.drawRect(10+x*self.cell_width,8+y*self.cell_height,self.cell_width,self.cell_height)
        p.end()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        metrics=QFontMetrics(self.terminal_font); self.cell_width=max(5,metrics.horizontalAdvance('M')); self.cell_height=metrics.height()+3
        columns=max(20,(self.viewport().width()-20)//self.cell_width)
        lines=max(4,(self.viewport().height()-16)//self.cell_height)
        if (columns,lines)!=(self.screen.columns,self.screen.lines):
            self.screen.resize(lines=lines,columns=columns); self.sizeChanged.emit(columns,lines)
            self.resize_timer.start(); self.refresh_display(force=True)

    def send_size(self):
        if not self.connected or not re.fullmatch(r'/dev/pts/[0-9]{1,6}',self.tty): return
        if self.resize_process.state()!=QProcess.ProcessState.NotRunning:
            self.resize_timer.start(); return
        self.resize_process.setProgram(self.adb)
        self.resize_process.setArguments(adb_arguments(['-s',self.serial,'shell','-T','sh','-s']))
        self.resize_process.start(); self.resize_timeout.start()

    def write_size(self):
        script=f'stty -F {self.tty} rows {self.screen.lines} cols {self.screen.columns}\nexit\n'
        self.resize_process.write(script.encode('ascii')); self.resize_process.closeWriteChannel()

    def selected_text(self):
        if not self.selection: return ''
        start,end=sorted(self.selection); rows=self.rows(); result=[]
        for y in range(start[0],min(end[0]+1,len(rows))):
            left=start[1] if y==start[0] else 0; right=end[1] if y==end[0] else self.screen.columns
            result.append(''.join(rows[y][x].data for x in range(left,right)).rstrip())
        return '\n'.join(result)

    def copy_selection(self):
        text=self.selected_text()
        if text: QApplication.clipboard().setText(text)

    def paste(self):
        if not self.connected: return
        text=QApplication.clipboard().text()
        if not text: return
        if len(text.encode('utf-8'))>65536:
            QMessageBox.information(self,'粘贴过长','单次粘贴最多 64 KB。'); return
        if any(ord(c)<32 and c not in '\t\r\n' for c in text) or '\x7f' in text:
            QMessageBox.information(self,'无法粘贴','内容含终端控制字符，请使用普通文本。'); return
        if '\n' in text or '\r' in text:
            answer=QMessageBox.question(self,'粘贴多行内容','多行内容可能立即执行命令，是否继续？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
            if answer!=QMessageBox.StandardButton.Yes: return
        text=text.replace('\r\n','\n').replace('\r','\n')
        data=text.encode('utf-8')
        if 2004<<5 in self.screen.mode: data=b'\x1b[200~'+data+b'\x1b[201~'
        self.send(data); self.setFocus()

    def keyPressEvent(self,event):
        key=event.key(); mods=event.modifiers()
        ctrl=bool(mods&Qt.KeyboardModifier.ControlModifier); shift=bool(mods&Qt.KeyboardModifier.ShiftModifier)
        if ctrl and shift and key==Qt.Key.Key_C: self.copy_selection(); return
        if (ctrl and shift and key==Qt.Key.Key_V) or (shift and key==Qt.Key.Key_Insert): self.paste(); return
        if shift and key in (Qt.Key.Key_PageUp,Qt.Key.Key_PageDown):
            delta=-self.screen.lines if key==Qt.Key.Key_PageUp else self.screen.lines
            self.verticalScrollBar().setValue(self.verticalScrollBar().value()+delta); return
        if not self.connected: return
        if ctrl and Qt.Key.Key_A<=key<=Qt.Key.Key_Z: self.send(bytes([key-Qt.Key.Key_A+1])); return
        special={Qt.Key.Key_Return:b'\n',Qt.Key.Key_Enter:b'\n',Qt.Key.Key_Backspace:b'\x7f',
                 Qt.Key.Key_Tab:b'\t',Qt.Key.Key_Backtab:b'\x1b[Z',Qt.Key.Key_Escape:b'\x1b',
                 Qt.Key.Key_Insert:b'\x1b[2~',Qt.Key.Key_Delete:b'\x1b[3~',Qt.Key.Key_PageUp:b'\x1b[5~',Qt.Key.Key_PageDown:b'\x1b[6~',
                 Qt.Key.Key_Home:b'\x1b[H',Qt.Key.Key_End:b'\x1b[F'}
        arrows={Qt.Key.Key_Up:'A',Qt.Key.Key_Down:'B',Qt.Key.Key_Right:'C',Qt.Key.Key_Left:'D'}
        functions={Qt.Key.Key_F1:b'\x1bOP',Qt.Key.Key_F2:b'\x1bOQ',Qt.Key.Key_F3:b'\x1bOR',Qt.Key.Key_F4:b'\x1bOS',
                   Qt.Key.Key_F5:b'\x1b[15~',Qt.Key.Key_F6:b'\x1b[17~',Qt.Key.Key_F7:b'\x1b[18~',Qt.Key.Key_F8:b'\x1b[19~',
                   Qt.Key.Key_F9:b'\x1b[20~',Qt.Key.Key_F10:b'\x1b[21~',Qt.Key.Key_F11:b'\x1b[23~',Qt.Key.Key_F12:b'\x1b[24~'}
        if key in arrows: data=('\x1bO' if 1<<5 in self.screen.mode else '\x1b[').encode()+arrows[key].encode()
        elif key in functions: data=functions[key]
        elif key in special: data=special[key]
        elif event.text(): data=event.text().encode('utf-8')
        else: return
        if mods&Qt.KeyboardModifier.AltModifier: data=b'\x1b'+data
        self.send(data)

    def focusNextPrevChild(self,next): return False

    def inputMethodEvent(self,event):
        if event.commitString(): self.send(event.commitString().encode('utf-8'))
        event.accept()

    def inputMethodQuery(self,query):
        if query==Qt.InputMethodQuery.ImCursorRectangle:
            return QRect(10+self.screen.cursor.x*self.cell_width,8+self.screen.cursor.y*self.cell_height,self.cell_width,self.cell_height)
        if query==Qt.InputMethodQuery.ImEnabled: return True
        return super().inputMethodQuery(query)

    def point(self,event):
        x=max(0,min(self.screen.columns,int((event.position().x()-10)//self.cell_width)))
        y=max(0,min(self.screen.lines-1,int((event.position().y()-8)//self.cell_height)))+self.verticalScrollBar().value()
        return y,x

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.setFocus(); self.anchor=self.point(event); self.selection=(self.anchor,self.anchor); self.viewport().update()

    def mouseMoveEvent(self,event):
        if event.buttons()&Qt.MouseButton.LeftButton and self.anchor is not None:
            self.selection=(self.anchor,self.point(event)); self.viewport().update()

    def wheelEvent(self,event):
        self.verticalScrollBar().setValue(self.verticalScrollBar().value()-event.angleDelta().y()//40)
