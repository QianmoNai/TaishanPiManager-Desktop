from collections import deque
import time
from PySide6.QtCore import Qt, QObject, Signal, QRunnable, QThreadPool, QTimer, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from selection_widgets import QComboBox
from PySide6.QtWidgets import (QWidget,QFrame,QLabel,QVBoxLayout,QHBoxLayout,QGridLayout,
    QPushButton,QLineEdit,QSpinBox,QMessageBox,QApplication,
    QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView)
from traffic import speed_test
from public_speed import public_speed_test,connectivity_test,NODES


def amount(value):
    for unit in ('B','KiB','MiB','GiB','TiB'):
        if value<1024 or unit=='TiB': return f'{value:.1f} {unit}'
        value/=1024


class Signals(QObject):
    done=Signal(object,object)
    progress=Signal(str)


class Task(QRunnable):
    def __init__(self,fn): super().__init__(); self.fn=fn; self.signals=Signals()
    def run(self):
        try: self.signals.done.emit(self.fn(),None)
        except Exception as e: self.signals.done.emit(None,str(e))


class Graph(QWidget):
    def __init__(self):
        super().__init__(); self.points=deque(maxlen=60); self.setMinimumHeight(145)
    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark=QApplication.instance().property('theme')=='dark'
        area=QRectF(75,25,max(10,self.width()-90),self.height()-50)
        peak=max(1024,max((max(r,t) for _,r,t in self.points),default=0)*1.2)
        p.setPen(QColor('#767680' if dark else '#86868b'))
        for i in range(3):
            y=area.top()+area.height()*i/2
            p.drawText(QRectF(0,y-10,70,20),Qt.AlignmentFlag.AlignRight,amount(peak*(1-i/2)))
            p.drawLine(int(area.left()),int(y),int(area.right()),int(y))
        p.drawText(QRectF(area.left(),area.bottom()+6,area.width(),20),'最近 120 秒 · 蓝色下载 / 绿色上传 · 单位 /s')
        if len(self.points)<2:
            p.drawText(area,Qt.AlignmentFlag.AlignCenter,'等待采样数据'); return
        end=self.points[-1][0]
        for index,color in ((1,'#007aff'),(2,'#30b86b')):
            path=QPainterPath(); started=False
            for point in self.points:
                x=area.right()-min(120,end-point[0])/120*area.width()
                y=area.bottom()-point[index]/peak*area.height()
                if not started: path.moveTo(x,y); started=True
                else: path.lineTo(x,y)
            p.setPen(QPen(QColor(color),2)); p.drawPath(path)


class TrafficPanel(QFrame):
    def __init__(self,owner):
        super().__init__(); self.owner=owner; self.setObjectName('card')
        self.pool=QThreadPool(self); self.pool.setMaxThreadCount(2); self.tasks={}; self.generation=0
        self.data=None; self.last_stamp=None; self.speed_busy=False; self.connectivity_busy=False
        box=QVBoxLayout(self); box.setContentsMargins(22,18,22,18); box.setSpacing(12)
        row=QHBoxLayout(); title=QLabel('网络流量'); title.setObjectName('section'); row.addWidget(title); row.addStretch()
        self.iface=QComboBox(); self.iface.setMinimumWidth(140); self.iface.currentIndexChanged.connect(self.change_interface)
        row.addWidget(QLabel('网卡')); row.addWidget(self.iface); box.addLayout(row)
        self.state=QLabel('安装并启动流量监控插件后显示数据。'); self.state.setWordWrap(True); box.addWidget(self.state)
        metrics=QGridLayout(); self.values=[]
        for i,name in enumerate(('↓ 下载速度','↑ 上传速度','累计下载','累计上传')):
            container=QVBoxLayout(); caption=QLabel(name); caption.setObjectName('subtle'); container.addWidget(caption)
            value=QLabel('—'); value.setStyleSheet('font-size:24px; font-weight:600;'); container.addWidget(value)
            self.values.append(value); metrics.addLayout(container,i//2,i%2)
        box.addLayout(metrics); self.graph=Graph(); box.addWidget(self.graph)
        self.note=QLabel('累计从首次启用统计起计算，按网卡分别保存；包含局域网、互联网及测速流量。')
        self.note.setWordWrap(True); self.note.setObjectName('caption'); box.addWidget(self.note)
        row=QHBoxLayout(); self.mode=QComboBox(); self.mode.addItems(['与本电脑测速（局域网）','指定 iperf3 服务器','一键公网测速'])
        row.addWidget(self.mode,1); self.start=QPushButton('开始测速'); self.start.setMinimumHeight(38)
        self.start.setProperty('kind','primary'); self.start.clicked.connect(self.test_speed); row.addWidget(self.start); box.addLayout(row)
        custom=QHBoxLayout(); self.host=QLineEdit(); self.host.setPlaceholderText('服务器 IPv4 / 域名（需运行 iperf3 -s）')
        self.port=QSpinBox(); self.port.setRange(1,65535); self.port.setValue(5201); custom.addWidget(self.host,1); custom.addWidget(self.port)
        box.addLayout(custom); self.host.hide(); self.port.hide()
        self.mode.currentIndexChanged.connect(lambda i:(self.host.setVisible(i==1),self.port.setVisible(i==1)))
        self.result=QLabel('上传、下载各测试 5 秒，结果以 Mbps 显示。局域网结果不代表宽带网速。')
        self.result.setWordWrap(True); self.result.setTextFormat(Qt.TextFormat.PlainText); box.addWidget(self.result)
        self.mode.currentIndexChanged.connect(self.mode_hint)
        row=QHBoxLayout(); title=QLabel('网络连通性测试'); title.setObjectName('section'); row.addWidget(title); row.addStretch()
        self.connectivity_start=QPushButton('测试节点连通性'); self.connectivity_start.setMinimumHeight(38)
        self.connectivity_start.clicked.connect(self.test_connectivity); row.addWidget(self.connectivity_start); box.addLayout(row)
        self.connectivity_hint=QLabel('由泰山派探测国内外节点。显示 HTTP 响应延迟（含 DNS、建连与响应），不是 ICMP ping；不下载测速文件。')
        self.connectivity_hint.setWordWrap(True); box.addWidget(self.connectivity_hint)
        self.connectivity_table=QTableWidget(len(NODES),4)
        self.connectivity_table.setHorizontalHeaderLabels(['节点','地址','状态','延迟'])
        self.connectivity_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.connectivity_table.verticalHeader().hide()
        self.connectivity_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents)
        self.connectivity_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        for col in (2,3): self.connectivity_table.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeMode.ResizeToContents)
        self.connectivity_table.setMinimumHeight(340); box.addWidget(self.connectivity_table)
        self.clear_connectivity()
        self.timer=QTimer(self); self.timer.setInterval(2000); self.timer.timeout.connect(self.poll); self.timer.start()

    def mode_hint(self):
        if self.speed_busy: return
        self.result.setText('由泰山派探测国内外节点并自动选点。每节点最多下载 16 MiB；失败时尝试下一节点。' if self.mode.currentIndex()==2 else '上传、下载各测试 5 秒，局域网结果不代表宽带网速。')

    def clear_connectivity(self,state='未测试'):
        for row,(name,host,port) in enumerate(NODES):
            for col,text in enumerate((name,f'{host}:{port}',state,'—')):
                self.connectivity_table.setItem(row,col,QTableWidgetItem(text))

    def test_connectivity(self):
        if self.connectivity_busy or self.speed_busy or not self.owner.require_device(): return
        serial=self.owner.serial; generation=self.generation; interface=self.iface.currentText()
        self.connectivity_busy=True; self.connectivity_start.setEnabled(False); self.start.setEnabled(False)
        self.clear_connectivity('检测中'); self.connectivity_hint.setText('正在探测全部节点，最多约 110 秒…')
        def done(data,error):
            self.connectivity_busy=False; self.connectivity_start.setEnabled(True); self.start.setEnabled(True)
            if serial!=self.owner.serial or generation!=self.generation: return
            if error:
                self.clear_connectivity('检测失败'); self.connectivity_hint.setText(error); return
            count=0
            for row,node in enumerate(data['nodes']):
                ok=node['reachable']; count+=int(ok)
                status=QTableWidgetItem('● 可达' if ok else '● 未通过探测')
                status.setForeground(QColor('#30b86b' if ok else '#e85d5d'))
                self.connectivity_table.setItem(row,2,status)
                self.connectivity_table.setItem(row,3,QTableWidgetItem(f'{node["latency_ms"]:.1f} ms' if ok else '—'))
            self.connectivity_hint.setText(f'{data["interface"]} · {count}/{len(NODES)} 节点可达 · HTTP 响应延迟，取两次探测较小值。未通过可能是超时、DNS 或 HTTP 接口不兼容。')
        self.run('connectivity',lambda:connectivity_test(self.owner.api.adb,serial,interface),done)

    def run(self,key,fn,callback,on_progress=None):
        if key in self.tasks: return
        task=Task(fn); self.tasks[key]=task
        if on_progress:
            task.fn=lambda:fn(task.signals.progress.emit)
            task.signals.progress.connect(on_progress)
        def done(data,error):
            self.tasks.pop(key,None); callback(data,error)
        task.signals.done.connect(done); self.pool.start(task)

    def reset(self):
        self.generation+=1; self.data=None; self.iface.clear(); self.last_stamp=None
        self.graph.points.clear(); self.graph.update()
        for v in self.values: v.setText('—')
        self.state.setText('等待当前设备的流量数据。')
        self.clear_connectivity(); self.connectivity_hint.setText('请测试当前设备的节点连通性。')
        if not self.speed_busy: self.result.setText('上传、下载各测试 5 秒，局域网结果不代表宽带网速。')

    def poll(self):
        if not self.isVisible() or not self.owner.serial or self.owner.busy or 'sample' in self.tasks: return
        serial=self.owner.serial; generation=self.generation
        def done(data,error):
            if serial!=self.owner.serial or generation!=self.generation: return
            if error: self.unavailable('连接中断，数据已过期：'+error); return
            self.render(data)
        self.run('sample',lambda:self.owner.api.dispatch('/api/traffic-status',{'serial':serial}),done)

    def unavailable(self,message):
        self.data=None; self.state.setText(message); self.last_stamp=None; self.graph.points.clear(); self.graph.update()
        for v in self.values: v.setText('—')

    def render(self,data):
        state=data.get('state')
        if state!='running':
            self.unavailable({'missing':'请在下方安装新版流量监控插件。','stopped':'监控已停止，请在下方启动服务。','stale':'采样已过期，请查看插件状态并重新启动。'}.get(state,'等待有效数据')); return
        self.data=data; names=sorted(data['interfaces']); current=self.iface.currentText()
        if names!=[self.iface.itemText(i) for i in range(self.iface.count())]:
            self.iface.blockSignals(True); self.iface.clear(); self.iface.addItems(names)
            preferred=current if current in names else max(names,key=lambda n:data['interfaces'][n]['rx']+data['interfaces'][n]['tx'],default='')
            self.iface.setCurrentText(preferred); self.iface.blockSignals(False)
        self.render_interface()

    def change_interface(self):
        self.generation+=1; self.clear_connectivity()
        self.graph.points.clear(); self.last_stamp=None; self.render_interface()

    def render_interface(self):
        if not self.data: return
        name=self.iface.currentText(); v=self.data['interfaces'].get(name)
        if not v: return
        for widget,value in zip(self.values,(amount(v['rx_rate'])+'/s',amount(v['tx_rate'])+'/s',amount(v['total_rx']),amount(v['total_tx']))): widget.setText(value)
        self.state.setText(('实时监控 · 每 2 秒更新 · '+name) if v['present'] else name+' 已移除，保留累计流量')
        if self.last_stamp!=self.data['uptime']:
            self.graph.points.append((self.data['uptime'],v['rx_rate'],v['tx_rate'])); self.last_stamp=self.data['uptime']; self.graph.update()
        started=time.strftime('%Y-%m-%d %H:%M',time.localtime(self.data['started']))
        self.note.setText('累计起点：'+started+' · 按网卡保存，每 60 秒落盘，正常停止时保存。突然断电可能丢失最近约 60 秒的统计。')

    def test_speed(self):
        if self.speed_busy or self.connectivity_busy or not self.owner.require_device(): return
        interface=self.iface.currentText()
        public=self.mode.currentIndex()==2
        if not interface and not public: self.result.setText('请先启动监控并选择网卡。'); return
        host=self.host.text().strip() if self.mode.currentIndex()==1 else ''
        if self.mode.currentIndex()==1 and not host: self.result.setText('请填写运行 iperf3 的服务器地址。'); return
        message=('将由泰山派连接苏州、昆山、东京 IPA CyberLab 和首尔 Kdatacenter 公网测速节点，先按延迟排序，再依次尝试。\n单节点最多下载 16 MiB、上传 4 MiB；节点失败会自动尝试下一个，结果只来自同一个成功节点。\n测试期间会占用带宽。公网节点可能受跨境链路、运营商策略和节点负载影响。' if public else '测速将占用所选网卡带宽并产生流量（不设流量上限），上传和下载各 5 秒。\n'+('目标：'+host if host else '测试泰山派与本电脑之间的局域网速度，泰山派会临时开启测速端口，测试结束自动关闭。'))
        if not self.owner.ask('开始公网测速' if public else '开始网速测试',message): return
        serial=self.owner.serial; generation=self.generation; port=self.port.value()
        self.speed_busy=True; self.start.setEnabled(False); self.mode.setEnabled(False)
        self.result.setText('正在测速… 最多约 180 秒。' if public else '正在测速… 最多约 35 秒，实时流量继续更新。')
        def done(data,error):
            self.speed_busy=False; self.start.setEnabled(True); self.mode.setEnabled(True)
            if serial!=self.owner.serial or generation!=self.generation:
                self.result.setText('设备已切换，已忽略上一设备的测速结果。'); return
            if error: self.result.setText(error); return
            extra=(f'\n{data["node"]} · HTTP 响应延迟 {data["latency_ms"]:.0f} ms\n短时单连接结果，受 Wi-Fi、跨境链路和节点负载影响。' if public else '')
            self.result.setText(f'下载 {data["download"]["mbps"]:.2f} Mbps    上传 {data["upload"]["mbps"]:.2f} Mbps'+extra+f'\n目标 {data["target"]} · {data["interface"]} · '+time.strftime('%H:%M:%S'))
        if public:
            def update(message):
                if serial==self.owner.serial and generation==self.generation: self.result.setText(message)
            self.run('speed',lambda report:public_speed_test(self.owner.api.adb,serial,interface,report),done,update)
        else: self.run('speed',lambda:speed_test(self.owner.api.adb,serial,interface,host,port),done)
