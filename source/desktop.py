"""Native Qt Widgets desktop application. No browser, webview or HTTP listener."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import time

from PySide6.QtCore import Qt, QObject, Signal, QRunnable, QThreadPool, QTimer, QSize, QRectF, QSettings
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap, QLinearGradient, QPen, QPalette
from PySide6.QtSvg import QSvgRenderer
from selection_widgets import QComboBox
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QGridLayout, QStackedWidget, QLineEdit, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox,
    QCheckBox, QProgressBar, QScrollArea, QAbstractItemView, QSizePolicy, QTabWidget,
)
from adb_core import Adb, App, UserError, MAX_TRANSFER, remote_path
from terminal_widget import Terminal
from traffic_widget import TrafficPanel
from plugin_center import PluginCenter
from proxy_widget import ProxyPanel
from network_status import NETWORK_STATUS_COMMAND, parse_network_status

ICONS = {
    'wifi': '<path d="M2 8a16 16 0 0 1 20 0M5 12a11 11 0 0 1 14 0m-11 4a6 6 0 0 1 8 0"/><circle cx="12" cy="20" r="1"/>',
    'moon': '<path d="M20.5 13A9 9 0 0 1 11 3.5 9 9 0 1 0 20.5 13Z"/>',
    'sun': '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
    'overview': '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    'folder': '<path d="M3 7V5a2 2 0 0 1 2-2h5l3 3h6a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>',
    'logs': '<rect x="5" y="3" width="14" height="18" rx="3"/><path d="M9 8h6M9 12h6M9 16h4"/>',
    'terminal': '<rect x="2" y="4" width="20" height="16" rx="3"/><path d="m6 9 3 3-3 3m7 0h5"/>',
    'settings': '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3" fill="white"/><circle cx="16" cy="17" r="3" fill="white"/>',
    'usb': '<path d="M12 21V3m-3 3 3-3 3 3M12 16l-6-4V8m6 4 6-4V6"/><circle cx="6" cy="7" r="1.5"/><rect x="16.5" y="3" width="3" height="3"/><circle cx="12" cy="20" r="1"/>',
    'refresh': '<path d="M20 10a8 8 0 1 0-1 8M20 3v7h-7"/>',
    'cpu': '<rect x="6" y="6" width="12" height="12" rx="3"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/>',
    'memory': '<rect x="3" y="6" width="18" height="12" rx="3"/><path d="M7 10v4m5-4v4m5-4v4M7 18v3m5-3v3m5-3v3"/>',
    'temperature': '<path d="M9 14V5a3 3 0 0 1 6 0v9a5 5 0 1 1-6 0ZM12 9v9"/>',
    'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
    'file': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/>',
    'power': '<path d="M12 2v10m-5-7a9 9 0 1 0 10 0"/>',
    'mountain': '<path d="m2 19 7-13 5 8 3-5 5 10H2Z"/>',
}


def icon(name, color='#007AFF', size=24):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><g fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</g></svg>'
    pix = QPixmap(size*2, size*2); pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix); QSvgRenderer(svg.encode()).render(painter); painter.end()
    pix.setDevicePixelRatio(2)
    return QIcon(pix)


def app_icon():
    pix = QPixmap(256,256); pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    grad = QLinearGradient(0,0,256,256); grad.setColorAt(0,QColor('#5aacff')); grad.setColorAt(1,QColor('#0062ed'))
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(grad); p.drawRoundedRect(QRectF(4,4,248,248),55,55)
    mountain = icon('mountain','#ffffff',180).pixmap(180,180)
    p.drawPixmap(38,35,mountain); p.end()
    return QIcon(pix)


def label(text='', kind=None, wrap=False):
    widget=QLabel(text); widget.setTextFormat(Qt.TextFormat.PlainText)
    if kind: widget.setObjectName(kind)
    widget.setWordWrap(wrap)
    return widget


def configure_app(app, settings=None):
    # Explicit registration also makes offscreen QA use the same Windows fonts.
    fonts=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
    for name in ('segoeui.ttf','segoeuib.ttf','msyh.ttc','msyhbd.ttc','consola.ttf','consolab.ttf','consolai.ttf'):
        path=fonts/name
        if path.exists(): QFontDatabase.addApplicationFont(str(path))
    app.setStyle('Fusion'); app.setFont(QFont('Microsoft YaHei UI',10))
    app.theme_settings = settings if settings is not None else QSettings('TaishanPi', 'DeviceManager')
    apply_theme(app, app.theme_settings.value('appearance/theme', 'light'))


def apply_theme(app, theme, save=False):
    theme = 'dark' if theme == 'dark' else 'light'
    dark = theme == 'dark'
    palette = app.style().standardPalette()
    if dark:
        for role, color in {
            'Window':'#161619', 'WindowText':'#f2f2f7', 'Base':'#242428',
            'AlternateBase':'#2c2c31', 'Text':'#f2f2f7', 'Button':'#2c2c31',
            'ButtonText':'#f2f2f7', 'Highlight':'#0a64ce', 'HighlightedText':'#ffffff',
            'ToolTipBase':'#2c2c31', 'ToolTipText':'#f2f2f7', 'PlaceholderText':'#94949e',
            'Light':'#484850', 'Mid':'#3a3a42', 'Dark':'#111114', 'Shadow':'#85858f',
        }.items(): palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
        for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
            palette.setColor(QPalette.ColorGroup.Disabled, role, QColor('#71717b'))
    app.setPalette(palette)
    app.setProperty('theme', theme)
    app.setStyleSheet(STYLE + (DARK_STYLE if dark else ''))
    if save:
        app.theme_settings.setValue('appearance/theme', theme)
        app.theme_settings.sync()
    for widget in app.topLevelWidgets():
        if isinstance(widget, Window): widget.sync_theme()


def theme_titlebar(window, dark):
    # Windows 10/11 title bar; offscreen rendering has no native decoration.
    if sys.platform != 'win32' or QApplication.platformName() == 'offscreen': return
    import ctypes
    value = ctypes.c_int(int(dark))
    for attribute in (20, 19):
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(int(window.winId())), attribute, ctypes.byref(value), ctypes.sizeof(value))
        if result == 0: break


def button(text, callback=None, kind='secondary', symbol=None):
    widget=QPushButton(text); widget.setProperty('kind',kind); widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.setMinimumHeight(38)
    if symbol: widget.setIcon(icon(symbol,'#ffffff' if kind=='primary' else '#007AFF',18))
    if callback: widget.clicked.connect(callback)
    return widget


def card():
    widget=QFrame(); widget.setObjectName('card')
    layout=QVBoxLayout(widget); layout.setContentsMargins(22,20,22,20); layout.setSpacing(14)
    return widget,layout


def bytes_text(value):
    value=float(value)
    for unit in ('B','KB','MB','GB','TB'):
        if value<1024 or unit=='TB': return f'{value:.1f} {unit}' if unit in ('MB','GB','TB') else f'{value:.0f} {unit}'
        value/=1024


class Signals(QObject):
    result=Signal(object)
    error=Signal(str)


class Job(QRunnable):
    def __init__(self, fn):
        super().__init__(); self.fn=fn; self.signals=Signals()

    def run(self):
        try: self.signals.result.emit(self.fn())
        except Exception as exc: self.signals.error.emit(str(exc) or '操作失败，请检查设备连接。')
        finally: self.fn = None


class DeviceArt(QWidget):
    def __init__(self):
        super().__init__(); self.setFixedSize(210,154)

    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(105,77); p.rotate(-9)
        dark = QApplication.instance().property('theme') == 'dark'
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor('#101a2a' if dark else '#d6e5fa')); p.drawRoundedRect(QRectF(-73,-49,156,111),20,20)
        grad=QLinearGradient(-80,-50,80,50); grad.setColorAt(0,QColor('#2a466d')); grad.setColorAt(1,QColor('#14223f'))
        p.setBrush(grad); p.drawRoundedRect(QRectF(-80,-57,156,108),17,17)
        p.setPen(QPen(QColor('#47739c'),1))
        for y in (-28,-16,20,32): p.drawLine(-65,y,60,y)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor('#91a7c0')); p.drawRoundedRect(QRectF(-33,-32,61,56),7,7)
        p.setBrush(QColor('#121e33')); p.drawRoundedRect(QRectF(-29,-28,53,48),5,5)
        p.setPen(QColor('#d4e8ff')); p.setFont(QFont('Segoe UI',9,QFont.Weight.DemiBold)); p.drawText(QRectF(-28,-28,50,48),Qt.AlignmentFlag.AlignCenter,'RK3566')
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor('#c6d4e8'))
        for y in (-37,3): p.drawRoundedRect(QRectF(66,y,22,29),3,3)
        p.setBrush(QColor('#d8c18a'))
        for x in range(-64,47,9): p.drawRoundedRect(QRectF(x,37,4,8),1,1)
        p.setBrush(QColor('#34e89a')); p.drawEllipse(QRectF(-66,-44,5,5)); p.end()


class Metric(QFrame):
    def __init__(self,title,symbol,color):
        super().__init__(); self.setObjectName('card'); self.setMinimumHeight(165)
        layout=QVBoxLayout(self); layout.setContentsMargins(20,17,20,17); layout.setSpacing(8)
        row=QHBoxLayout(); tile=label(); tile.setPixmap(icon(symbol,color,21).pixmap(21,21)); row.addWidget(tile); row.addWidget(label(title,'subtle')); row.addStretch(); layout.addLayout(row)
        self.value=label('—','metricValue'); self.value.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred); layout.addWidget(self.value)
        self.bar=QProgressBar(); self.bar.setRange(0,1000); self.bar.setValue(0); self.bar.setTextVisible(False); self.bar.setFixedHeight(5); layout.addWidget(self.bar)
        self.detail=label('等待设备连接','caption'); self.detail.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred); layout.addWidget(self.detail)


class Window(QMainWindow):
    def __init__(self, backend=None, autostart=True):
        super().__init__(); self.api=backend or App(Adb()); self.serial=''; self.devices=[]; self.busy=False; self.job=None
        self.previous_cpu=None; self.current_dir='/userdata'; self.file_serial=''; self.rows=[]; self.pool=QThreadPool(self); self.pool.setMaxThreadCount(1)
        self.setWindowTitle('泰山派 · 设备管理'); self.setWindowIcon(app_icon()); self.resize(1200,840); self.setMinimumSize(1000,740)
        root=QWidget(); root.setObjectName('root'); self.setCentralWidget(root)
        body=QHBoxLayout(root); body.setContentsMargins(0,0,0,0); body.setSpacing(0)
        sidebar=QFrame(); sidebar.setObjectName('sidebar'); sidebar.setFixedWidth(214)
        side=QVBoxLayout(sidebar); side.setContentsMargins(18,30,18,24); side.setSpacing(8)
        brand=QHBoxLayout(); logo=label(); logo.setPixmap(app_icon().pixmap(40,40)); brand.addWidget(logo)
        names=QVBoxLayout(); names.setSpacing(2); names.addWidget(label('泰山派','brand')); names.addWidget(label('Device Manager','caption')); brand.addLayout(names); brand.addStretch(); side.addLayout(brand)
        side.addSpacing(30); side.addWidget(label('  设备管理','sideHeading')); side.addSpacing(5)
        self.nav=[]
        for idx,(name,symbol) in enumerate([('设备概览','overview'),('文件管理','folder'),('系统日志','logs'),('终端','terminal'),('插件中心','settings'),('网络设置','wifi')]):
            btn=button('  '+name,lambda checked=False,i=idx:self.go(i),'nav',symbol); btn.setMinimumHeight(46); btn.setCheckable(True); self.nav.append(btn); side.addWidget(btn)
        side.addStretch()
        self.theme_btn=button('深色模式', self.toggle_theme, symbol='moon'); side.addWidget(self.theme_btn); side.addSpacing(12)
        side.addWidget(label('●  本机独立应用','sideStatus')); side.addWidget(label('USB / 网络 ADB · v2.23','caption')); body.addWidget(sidebar)
        content=QWidget(); outer=QVBoxLayout(content); outer.setContentsMargins(30,28,30,16); outer.setSpacing(17); body.addWidget(content,1)
        heading=QHBoxLayout(); titlebox=QVBoxLayout(); titlebox.setSpacing(4); self.title=label('设备概览','title'); self.subtitle=label('一眼掌握，设备的每个状态。','subtle'); titlebox.addWidget(self.title); titlebox.addWidget(self.subtitle); heading.addLayout(titlebox); heading.addStretch()
        self.badge=label('●  未连接','badge'); heading.addWidget(self.badge,0,Qt.AlignmentFlag.AlignTop); outer.addLayout(heading)
        connection,connection_layout=card(); connection_layout.setContentsMargins(18,14,18,13); connection_layout.setSpacing(9)
        line=QHBoxLayout(); line.setSpacing(10); usb=label(); usb.setPixmap(icon('usb',size=22).pixmap(22,22)); line.addWidget(usb)
        self.device_select=QComboBox(); self.device_select.setMinimumHeight(38); self.device_select.addItem('连接 USB 后，刷新设备',''); self.device_select.currentIndexChanged.connect(self.select_device); line.addWidget(self.device_select,1)
        self.refresh_btn=button('刷新设备',self.refresh,symbol='refresh'); line.addWidget(self.refresh_btn)
        self.net_btn=button('无线连接',self.toggle_network); line.addWidget(self.net_btn); connection_layout.addLayout(line)
        self.connection_hint=label('用 USB 数据线连接开发板的 OTG 接口；选择 USB 设备后会自动读取局域网 IP。','caption',True); connection_layout.addWidget(self.connection_hint)
        self.net_panel=QWidget(); net=QHBoxLayout(self.net_panel); net.setContentsMargins(0,5,0,0); self.address=QLineEdit(''); self.address.setPlaceholderText('自动读取设备 IP:5555，也可手动填写'); self.connect_btn=button('连接',self.connect_network,'primary'); net.addWidget(self.address,1); net.addWidget(self.connect_btn); self.net_panel.hide(); connection_layout.addWidget(self.net_panel); outer.addWidget(connection)
        self.banner=label('','notice',True); self.banner.hide(); outer.addWidget(self.banner)
        self.stack=QStackedWidget(); outer.addWidget(self.stack,1)
        self.build_overview(); self.build_files(); self.build_logs(); self.build_terminal(); self.build_services(); self.build_wifi()
        footer=QHBoxLayout(); self.activity=label('就绪','caption'); footer.addWidget(self.activity); footer.addStretch(); footer.addWidget(label('设备数据直连 · 不使用浏览器','caption')); outer.addLayout(footer)
        self.guarded=[self.device_select,self.refresh_btn,self.connect_btn,self.open_btn,self.up_btn,self.upload_btn,self.download_btn,self.log_btn,self.reboot_btn,*self.service_buttons,self.wifi_iface,self.wifi_scan_btn,self.wifi_status_btn,self.wifi_connect_btn,self.wifi_table,self.wifi_password,self.wifi_show_password]
        self.guarded.extend([self.install_monitor_btn,self.monitor_autostart,self.plugin_center.refresh,self.plugin_primary,self.uninstall_monitor_btn])
        self.guarded.extend(self.proxy.controls)
        self.timer=QTimer(self); self.timer.setInterval(5000); self.timer.timeout.connect(self.poll); self.timer.start(); self.go(0,False)
        self.sync_theme()
        if autostart: QTimer.singleShot(100,self.refresh)

    def toggle_theme(self):
        app = QApplication.instance()
        apply_theme(app, 'light' if app.property('theme') == 'dark' else 'dark', save=True)

    def sync_theme(self):
        dark = QApplication.instance().property('theme') == 'dark'
        self.theme_btn.setText('浅色模式' if dark else '深色模式')
        self.theme_btn.setIcon(icon('sun' if dark else 'moon', '#64aaff' if dark else '#007aff', 18))
        self.theme_btn.setToolTip('当前为深色，点击切换浅色' if dark else '当前为浅色，点击切换深色')
        theme_titlebar(self, dark)
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        theme_titlebar(self, QApplication.instance().property('theme') == 'dark')

    def scroll_page(self):
        area=QScrollArea(); area.setWidgetResizable(True); area.setFrameShape(QFrame.Shape.NoFrame); area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        widget=QWidget(); widget.setObjectName('page'); layout=QVBoxLayout(widget); layout.setContentsMargins(0,0,5,0); layout.setSpacing(16); area.setWidget(widget); self.stack.addWidget(area)
        return layout

    def build_overview(self):
        layout=self.scroll_page(); hero=QFrame(); hero.setObjectName('hero'); row=QHBoxLayout(hero); row.setContentsMargins(27,16,24,16)
        text=QVBoxLayout(); text.setSpacing(8); text.addWidget(label('TAISHANPI  /  RK3566','eyebrow')); self.hostname=label('你好，泰山派。','heroTitle'); text.addWidget(self.hostname); self.os_label=label('连接设备，开始你的工作。','heroSub',True); text.addWidget(self.os_label); self.hero_tag=label('USB 即连即用   ·   Linux / Buildroot','heroCaption'); text.addWidget(self.hero_tag); row.addLayout(text,1); row.addWidget(DeviceArt()); layout.addWidget(hero)
        metrics=QHBoxLayout(); metrics.setSpacing(12); self.metrics=[]
        for name,symbol,color in [('CPU 使用率','cpu','#007aff'),('内存使用','memory','#af52de'),('芯片温度','temperature','#ff9500'),('运行时间','clock','#34a474')]:
            metric=Metric(name,symbol,color); metrics.addWidget(metric,1); self.metrics.append(metric)
        layout.addLayout(metrics)
        details=QHBoxLayout(); details.setSpacing(16)
        storage,storage_layout=card(); storage_layout.addWidget(label('存储空间','section')); self.storage_box=QVBoxLayout(); self.storage_box.setSpacing(13); self.storage_empty=label('连接后显示系统与 userdata 分区。','subtle',True); self.storage_box.addWidget(self.storage_empty); storage_layout.addLayout(self.storage_box); storage_layout.addStretch(); details.addWidget(storage,1)
        info,info_layout=card(); info_layout.addWidget(label('设备信息','section')); self.info_labels={}
        for key,title in [('serial','序列号'),('kernel','内核版本'),('network','网络接口')]:
            info_layout.addWidget(label(title,'caption')); value=label('—','infoValue',True); value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); self.info_labels[key]=value; info_layout.addWidget(value)
        info_layout.addStretch(); details.addWidget(info,1); layout.addLayout(details)
        layout.addWidget(label('状态每 5 秒刷新。文件、日志与命令按需读取。','caption')); layout.addStretch()

    def build_files(self):
        layout=self.scroll_page(); frame,box=card(); box.addWidget(label('你的设备文件','section'))
        row=QHBoxLayout(); self.up_btn=button('上级',self.parent_dir); self.path_edit=QLineEdit('/userdata'); self.path_edit.setAccessibleName('远端目录'); self.path_edit.returnPressed.connect(self.load_files)
        self.open_btn=button('打开',self.load_files,'primary'); row.addWidget(self.up_btn); row.addWidget(self.path_edit,1); row.addWidget(self.open_btn); box.addLayout(row)
        actions=QHBoxLayout(); self.upload_btn=button('上传文件',self.upload); self.download_btn=button('下载选中文件',self.download); actions.addWidget(self.upload_btn); actions.addWidget(self.download_btn); actions.addStretch(); box.addLayout(actions)
        self.table=QTableWidget(0,3); self.table.setHorizontalHeaderLabels(['名称','大小','修改时间']); self.table.verticalHeader().hide(); self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection); self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch); self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.ResizeToContents); self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(260); self.table.cellDoubleClicked.connect(self.open_entry); self.table.verticalHeader().setDefaultSectionSize(45); box.addWidget(self.table)
        self.file_hint=label('选择设备后打开目录。双击文件夹进入。','caption',True); box.addWidget(self.file_hint); layout.addWidget(frame); layout.addStretch()

    def console(self,placeholder):
        edit=QPlainTextEdit(); edit.setObjectName('console'); edit.setReadOnly(True); edit.setPlaceholderText(placeholder); edit.setMinimumHeight(250); edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth); return edit

    def build_logs(self):
        layout=self.scroll_page(); frame,box=card(); box.addWidget(label('系统日志','section')); row=QHBoxLayout(); self.log_kind=QComboBox()
        for title,key in [('内核日志 · dmesg','kernel'),('系统日志 · messages','system'),('网络健康监控','monitor'),('监控启动日志','autostart')]: self.log_kind.addItem(title,key)
        row.addWidget(self.log_kind,1); self.log_btn=button('读取日志',self.logs,'primary'); row.addWidget(self.log_btn); row.addWidget(button('另存为…',self.save_logs)); box.addLayout(row)
        self.log_output=self.console('最近 250 行日志会显示在这里。'); box.addWidget(self.log_output); box.addWidget(label('新固件可能尚未安装网络监控脚本，对应日志会提示不存在。','caption',True)); layout.addWidget(frame); layout.addStretch()

    def build_terminal(self):
        page=QWidget(); page.setObjectName('page'); box=QVBoxLayout(page); box.setContentsMargins(0,0,0,0); box.setSpacing(12); self.stack.addWidget(page)
        row=QHBoxLayout(); self.terminal_status=label('未连接终端','subtle'); row.addWidget(self.terminal_status,1)
        self.terminal_size=label('','caption'); row.addWidget(self.terminal_size)
        self.terminal_stop=button('断开',self.stop_terminal); self.terminal_stop.setEnabled(False); row.addWidget(self.terminal_stop); box.addLayout(row)
        tab_row=QHBoxLayout(); tab_row.addWidget(label('终端标签页','section')); tab_row.addStretch()
        tab_row.addWidget(button('新建标签页',self.new_terminal_tab)); tab_row.addWidget(button('关闭当前标签页',self.close_current_terminal_tab)); box.addLayout(tab_row)
        self.terminal_tabs=QTabWidget(); self.terminal_tabs.setTabsClosable(True); self.terminal_tabs.tabCloseRequested.connect(self.close_terminal_tab); self.terminal_tabs.currentChanged.connect(self.current_terminal_changed); box.addWidget(self.terminal_tabs,1)
        self.terminals=[]; self.terminal_counter=0; self.new_terminal_tab()
        row=QHBoxLayout(); row.addWidget(button('中断 Ctrl+C',lambda:self.terminal.send(b'\x03')))
        row.addWidget(button('复制选中',lambda:self.terminal.copy_selection())); row.addWidget(button('粘贴',lambda:self.terminal.paste())); row.addWidget(button('清空显示',lambda:self.terminal.clear_screen())); row.addStretch(); box.addLayout(row)
        box.addWidget(label('Enter 执行 · ↑↓ 历史 · Tab 补全 · Ctrl+Shift+C / V 复制粘贴 · 滚轮查看历史','caption',True))
        box.addWidget(label('选择设备后标签页自动连接；新建标签页也会自动连接。每个标签页是独立 ADB Shell 会话，可同时连接多个终端。切换设备会关闭全部会话；后台程序可能继续运行。','caption',True))

    def new_terminal_tab(self):
        term=Terminal(); term.setMinimumHeight(200); self.terminals.append(term)
        self.terminal_counter+=1; term.tab_title=f'终端 {self.terminal_counter}'
        index=self.terminal_tabs.addTab(term,term.tab_title); self.terminal_tabs.setCurrentIndex(index)
        term.connectionChanged.connect(lambda connected,message,t=term:self.terminal_state(connected,message,t))
        term.sizeChanged.connect(lambda cols,rows,t=term:self.terminal_size_changed(cols,rows,t))
        self.current_terminal_changed(index)
        if self.serial and self.api.adb.path:
            QTimer.singleShot(0,lambda t=term:t.connect_device(self.api.adb.path,self.serial))

    def current_terminal_changed(self,index):
        if not hasattr(self,'terminal_tabs') or index<0: return
        self.terminal=self.terminal_tabs.widget(index); self.terminal_status.setText(('已连接 · '+self.terminal.serial) if self.terminal.connected else '未连接终端')
        self.terminal_size.setText(f'{self.terminal.screen.columns} × {self.terminal.screen.lines}')
        self.terminal_stop.setEnabled(self.terminal.connected); self.terminal.setFocus()

    def terminal_size_changed(self,cols,rows,term):
        if term is self.terminal: self.terminal_size.setText(f'{cols} × {rows}')

    def close_terminal_tab(self,index):
        term=self.terminal_tabs.widget(index)
        if term is None: return
        if term.is_active() and not self.ask('关闭终端标签页','当前终端会话将结束，前台任务可能被中断。后台任务不保证停止。'): return
        term.disconnect_device(); self.terminals.remove(term); self.terminal_tabs.removeTab(index); term.deleteLater()
        if not self.terminals: self.new_terminal_tab()
        self.current_terminal_changed(self.terminal_tabs.currentIndex())

    def close_current_terminal_tab(self): self.close_terminal_tab(self.terminal_tabs.currentIndex())

    def terminal_state(self,connected,message,term=None):
        term=term or self.terminal; index=self.terminal_tabs.indexOf(term)
        if index>=0:
            self.terminal_tabs.setTabText(index,('● ' if connected else '')+term.tab_title)
            self.terminal_tabs.setTabToolTip(index,message)
        if term is self.terminal: self.terminal_status.setText(message); self.terminal_stop.setEnabled(connected)

    def stop_terminal(self):
        if self.terminal.connected and not self.ask('断开终端','当前终端会话将结束，前台任务可能被中断。后台任务不保证停止。'): return
        self.terminal.disconnect_device()

    def build_services(self):
        layout=self.scroll_page()
        self.plugin_center=PluginCenter(self,label,button,card,icon); layout.addWidget(self.plugin_center)
        self.plugin_detail=QWidget(); detail=QVBoxLayout(self.plugin_detail); detail.setContentsMargins(0,0,0,0); detail.setSpacing(16)
        row=QHBoxLayout(); row.addWidget(button('‹ 返回插件中心',self.close_plugin)); row.addStretch(); row.addWidget(label('网络工具 / 网络流量','caption')); detail.addLayout(row)
        frame,box=card(); row=QHBoxLayout(); tile=label(); tile.setPixmap(icon('wifi','#007aff',44).pixmap(44,44)); row.addWidget(tile)
        titles=QVBoxLayout(); titles.addWidget(label('网络流量','title')); titles.addWidget(label('v2.0 · 板端插件 · 本地安装','subtle')); row.addLayout(titles,1)
        self.plugin_primary=button('查看状态',self.plugin_primary_action,'primary'); row.addWidget(self.plugin_primary)
        row.addWidget(button('管理',self.show_plugin_management))
        self.uninstall_monitor_btn=button('卸载插件',self.uninstall_monitor,'danger'); row.addWidget(self.uninstall_monitor_btn); box.addLayout(row)
        box.addWidget(label('实时上传与下载速度、双向网速测试、跨重启累计流量。','subtle',True))
        box.addWidget(label('适用：Buildroot / root ADB · 统计保存在设备本地 · 无需修改 SDK','caption',True)); detail.addWidget(frame)
        self.traffic=TrafficPanel(self); detail.addWidget(self.traffic)
        frame,box=card(); self.plugin_management=frame; box.addWidget(label('安装与服务管理','section')); self.monitor_label=label('安装并启动后，可在上方查看实时网络数据。','subtle',True); box.addWidget(self.monitor_label)
        install_row=QHBoxLayout(); self.install_monitor_btn=button('安装 / 升级插件',self.install_monitor,'primary','settings'); install_row.addWidget(self.install_monitor_btn)
        self.monitor_autostart=QCheckBox('安装时启用开机自动启动'); install_row.addWidget(self.monitor_autostart); install_row.addStretch(); box.addLayout(install_row)
        box.addWidget(label('安装前请停止旧服务。安装会校验文件并备份原版本，完成后手动启动。','caption',True))
        row=QHBoxLayout(); self.service_buttons=[]
        for title,action in [('查看状态','status'),('启动服务','start'),('停止服务','stop')]:
            btn=button(title,lambda checked=False,a=action:self.service(a),'primary' if action=='start' else 'secondary'); self.service_buttons.append(btn); row.addWidget(btn)
        row.addStretch(); box.addLayout(row); self.service_output=self.console('连接设备后查看服务状态。'); self.service_output.setMinimumHeight(100); box.addWidget(self.service_output); detail.addWidget(frame)
        layout.addWidget(self.plugin_detail); self.plugin_detail.hide(); self.plugin_center.detail_callback=self.open_plugin
        self.proxy=ProxyPanel(self,label,button,card); layout.addWidget(self.proxy); self.proxy.hide()
        layout.addStretch()

    def open_proxy(self):
        self.plugin_detail.hide(); self.plugin_center.hide(); self.proxy.show()
        self.stack.widget(4).verticalScrollBar().setValue(0)
        self.title.setText('网络代理'); self.subtitle.setText('Mihomo · 配置、模式与节点管理。')
        self.proxy.action('status')

    def open_plugin(self):
        self.proxy.hide()
        self.plugin_center.hide(); self.plugin_detail.show()
        self.stack.widget(4).verticalScrollBar().setValue(0)
        self.title.setText('网络流量'); self.subtitle.setText('插件详情 · 速度、测速与累计流量。')
        QTimer.singleShot(0,self.traffic.poll)

    def show_plugin_management(self):
        self.stack.widget(4).ensureWidgetVisible(self.plugin_management,0,12)

    def plugin_primary_action(self):
        state=self.plugin_center.state
        if state in ('missing','update'): self.install_monitor()
        elif state=='stopped': self.service('start')
        elif state=='running': self.show_plugin_management()
        else: self.refresh_plugins()

    def close_plugin(self):
        self.proxy.hide()
        self.plugin_detail.hide(); self.plugin_center.show()
        self.stack.widget(4).verticalScrollBar().setValue(0)
        self.title.setText('插件中心'); self.subtitle.setText('发现、安装与管理你的设备工具。')
        self.refresh_plugins()

    def refresh_plugins(self):
        if not self.serial:
            self.plugin_center.render({'state':'offline'}); return
        if self.busy: return
        self.plugin_center.render({'state':'unknown'})
        self.plugin_center.hint.setText('正在读取当前设备的插件状态…')
        self.work(lambda:{**self.call('plugin-status'),'proxy':self.call('proxy-status')},self.plugin_center.render,'正在刷新插件状态…')

    def build_wifi(self):
        self.wifi_scan_serial=''; self.wifi_scan_iface=''; self.wifi_pending=False
        layout=self.scroll_page()
        frame,box=card(); row=QHBoxLayout(); row.addWidget(label('网络设置','title')); row.addStretch(); self.net_status_btn=button('刷新网络状态',self.refresh_network_status,symbol='refresh'); row.addWidget(self.net_status_btn); box.addLayout(row)
        self.net_summary=label('连接设备后查看 Wi-Fi、USB 网卡和默认路由。','subtle',True); box.addWidget(self.net_summary)
        self.net_table=QTableWidget(0,5); self.net_table.setHorizontalHeaderLabels(['接口','类型','链路','IPv4 地址','默认路由']); self.net_table.verticalHeader().hide(); self.net_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.net_table.setMinimumHeight(150); box.addWidget(self.net_table); layout.addWidget(frame)
        self.net_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.net_table.verticalHeader().setDefaultSectionSize(43)
        self.net_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.net_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        frame,box=card(); row=QHBoxLayout(); row.addWidget(label('附近的 Wi-Fi','section')); row.addStretch()
        self.wifi_iface=QComboBox(); self.wifi_iface.addItem('自动选择网卡',''); self.wifi_iface.setAccessibleName('无线网卡')
        self.wifi_iface.currentIndexChanged.connect(self.clear_wifi_selection); row.addWidget(self.wifi_iface)
        self.wifi_scan_btn=button('扫描附近 Wi-Fi',self.scan_wifi,'primary','wifi'); row.addWidget(self.wifi_scan_btn)
        self.wifi_status_btn=button('刷新状态',self.refresh_wifi,symbol='refresh'); row.addWidget(self.wifi_status_btn); box.addLayout(row)
        self.wifi_status=label('连接泰山派后，扫描设备附近的无线网络。','subtle',True); box.addWidget(self.wifi_status)
        self.wifi_table=QTableWidget(0,4); self.wifi_table.setHorizontalHeaderLabels(['Wi-Fi 名称','信号','频段','安全性'])
        self.wifi_table.verticalHeader().hide(); self.wifi_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.wifi_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection); self.wifi_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.wifi_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        for col in (1,2,3): self.wifi_table.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeMode.ResizeToContents)
        self.wifi_table.verticalHeader().setDefaultSectionSize(43); self.wifi_table.setMinimumHeight(190)
        self.wifi_table.itemSelectionChanged.connect(self.select_wifi); box.addWidget(self.wifi_table)
        self.wifi_hint=label('列表来自泰山派的无线网卡，点击扫描开始。','caption',True); box.addWidget(self.wifi_hint); layout.addWidget(frame)
        frame,box=card(); self.wifi_selected=label('选择一个 Wi-Fi 网络','section',True); box.addWidget(self.wifi_selected)
        row=QHBoxLayout(); self.wifi_password=QLineEdit(); self.wifi_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.wifi_password.setPlaceholderText('输入 Wi-Fi 密码'); self.wifi_password.setAccessibleName('Wi-Fi 密码'); self.wifi_password.setMaxLength(128)
        self.wifi_show_password=QCheckBox('显示密码'); self.wifi_show_password.toggled.connect(lambda checked:self.wifi_password.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
        self.wifi_connect_btn=button('连接 Wi-Fi',self.connect_wifi,'primary'); row.addWidget(self.wifi_password,1); row.addWidget(self.wifi_show_password); row.addWidget(self.wifi_connect_btn); box.addLayout(row)
        box.addWidget(label('建议使用 USB 连接。切换 Wi-Fi 可能中断网络 ADB；密码不保存在电脑上。','caption',True))
        box.addWidget(label('连接成功后会保存到泰山派的 wpa_supplicant 配置，重新上电将自动连接。','caption',True)); layout.addWidget(frame); layout.addStretch()

    def clear_wifi_selection(self, *args):
        self.wifi_scan_serial=''; self.wifi_scan_iface=''; self.wifi_table.setRowCount(0)
        self.wifi_password.clear(); self.wifi_show_password.setChecked(False)
        self.wifi_selected.setText('选择一个 Wi-Fi 网络'); self.wifi_hint.setText('请扫描当前网卡附近的网络。')
        self.wifi_status.setText('尚未获取当前网卡的连接状态。')

    def selected_wifi(self):
        row=self.wifi_table.currentRow()
        item=self.wifi_table.item(row,0) if row>=0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def select_wifi(self):
        network=self.selected_wifi(); self.wifi_password.clear(); self.wifi_show_password.setChecked(False)
        self.wifi_selected.setText('连接到 '+(network['ssid'] or '隐藏网络') if network else '选择一个 Wi-Fi 网络')
        self.wifi_password.setPlaceholderText('开放网络无需密码' if network and network['security']=='open' else '输入 Wi-Fi 密码')
        self.wifi_password.setReadOnly(bool(network and network['security']!='psk'))

    def scan_wifi(self):
        if not self.require_device() or self.busy: return
        data={'interface':self.wifi_iface.currentData() or ''}
        self.clear_wifi_selection()
        self.wifi_status.setText('正在扫描泰山派附近的 Wi-Fi…')
        self.wifi_pending=True
        self.work(lambda:self.call('wifi-scan',data),self.render_wifi_scan,'正在扫描 Wi-Fi…')

    def refresh_wifi(self):
        if not self.require_device() or self.busy: return
        data={'interface':self.wifi_iface.currentData() or ''}
        self.wifi_pending=True
        self.work(lambda:self.call('wifi-status',data),self.render_wifi_status,'正在读取 Wi-Fi 状态…')

    def refresh_network_status(self):
        if not self.require_device() or self.busy: return
        def done(raw):
            rows,default=parse_network_status(raw.decode('utf-8','replace'))
            self.net_table.setRowCount(len(rows))
            for r,row in enumerate(rows):
                for c,value in enumerate(row):
                    item=QTableWidgetItem(value); item.setToolTip(value); self.net_table.setItem(r,c,item)
            if not rows: self.net_summary.setText('未检测到网络接口，请检查 USB 网卡、网线、Wi-Fi 驱动或设备连接。')
            elif not any(row[3] != '—' for row in rows): self.net_summary.setText('检测到网卡，但没有 IPv4 地址；请检查网线、DHCP 或 Wi-Fi 连接。')
            elif not default: self.net_summary.setText('网络接口已发现，但没有默认路由，当前可能无法访问局域网或互联网。')
            else: self.net_summary.setText(f'默认路由：{default} · USB 网卡通常为 eth1 或 enx…；支持 DHCP 自动获取地址。')
        self.work(lambda:self.api.adb.shell(self.serial,NETWORK_STATUS_COMMAND,timeout=5)[0],done,'正在读取网络接口状态…')

    def render_wifi_status(self,data):
        self.wifi_iface.blockSignals(True); self.wifi_iface.clear()
        for name in data['interfaces']: self.wifi_iface.addItem(name,name)
        self.wifi_iface.setCurrentIndex(max(0,self.wifi_iface.findData(data['interface']))); self.wifi_iface.blockSignals(False)
        states={'COMPLETED':'已认证','DISCONNECTED':'未连接','INACTIVE':'未连接','SCANNING':'扫描中','ASSOCIATING':'连接中','ASSOCIATED':'已关联','4WAY_HANDSHAKE':'正在认证','GROUP_HANDSHAKE':'正在认证','INTERFACE_DISABLED':'无线网卡已禁用'}
        if data['state']=='COMPLETED':
            text=f'{data["interface"]} · {data["ssid"]} · '+('已连接 · '+data['ip'] if data['ip'] else '已认证，等待 IPv4 地址')
        else: text=f'{data["interface"]} · '+states.get(data['state'],'状态：'+data['state'])
        self.wifi_status.setText(text)

    def render_wifi_scan(self,data):
        self.render_wifi_status(data); self.wifi_scan_serial=self.serial; self.wifi_scan_iface=data['interface']
        self.wifi_table.setRowCount(len(data['networks']))
        for row,network in enumerate(data['networks']):
            item=QTableWidgetItem(network['ssid'] or '隐藏网络（暂不支持）'); item.setData(Qt.ItemDataRole.UserRole,network)
            item.setToolTip('BSSID: '+network['bssid']); self.wifi_table.setItem(row,0,item)
            level='强' if network['signal']>=-55 else ('中' if network['signal']>=-70 else '弱')
            band='2.4 GHz' if network['frequency']<3000 else ('5 GHz' if network['frequency']<5925 else '6 GHz')
            for col,value in enumerate([f'{level} · {network["signal"]} dBm',band,network['security_label']],1): self.wifi_table.setItem(row,col,QTableWidgetItem(value))
        self.wifi_hint.setText(f'发现 {len(data["networks"])} 个接入点 · 同名 Wi-Fi 可能来自不同路由器。' if data['networks'] else '未发现网络，请确认天线、距离或稍后重新扫描。')

    def connect_wifi(self):
        if not self.require_device() or self.busy: return
        network=self.selected_wifi()
        if not network or self.wifi_scan_serial!=self.serial or self.wifi_scan_iface!=(self.wifi_iface.currentData() or ''):
            self.notify('请先扫描并选择当前设备的 Wi-Fi 网络。',True); return
        if not network['ssid_hex'] or network['security'] not in ('psk','open'):
            self.notify('暂不支持隐藏网络、企业认证或纯 WPA3 网络，请选择 WPA/WPA2 个人网络或开放网络。',True); return
        if network['security']=='psk' and not self.wifi_password.text(): self.notify('请输入 Wi-Fi 密码。',True); self.wifi_password.setFocus(); return
        transport=next((d['transport'] for d in self.devices if d['serial']==self.serial),'')
        warnings=[]
        if transport=='网络' or ':' in self.serial: warnings.append('当前使用网络 ADB，切换 Wi-Fi 可能立即断开管理连接。建议通过 USB 操作。')
        if network['security']=='open': warnings.append('这是不加密的开放网络。')
        if warnings and not self.ask('连接 Wi-Fi', '\n'.join(warnings)+'\n连接到：'+network['ssid']): return
        data={'interface':self.wifi_scan_iface,'ssid_hex':network['ssid_hex'],'security':network['security'],'password':self.wifi_password.text()}
        self.wifi_password.clear(); self.wifi_show_password.setChecked(False); self.wifi_status.setText('正在连接 '+network['ssid']+'，请等待认证和地址分配…')
        def operation():
            try: return self.call('wifi-connect',data)
            finally: data['password']=''
        def connected(result): self.render_wifi_status(result); self.notify(result['message'])
        self.wifi_pending=True
        self.work(operation,connected,'正在连接 Wi-Fi，最多约 1 分钟…')

    def notify(self,text,error=False):
        self.banner.setText(text); self.banner.setProperty('error',error); self.banner.style().unpolish(self.banner); self.banner.style().polish(self.banner); self.banner.show()

    def go(self,index,refresh=True):
        self.stack.setCurrentIndex(index)
        names=[('设备概览','一眼掌握，设备的每个状态。'),('文件管理','在设备与电脑之间，轻松传输。'),('系统日志','让每一个问题，有迹可循。'),('终端','持续会话，实时交互。'),('插件中心','发现、安装与管理你的设备工具。'),('网络设置','管理 Wi-Fi、USB 网卡与网络路由。')]
        self.title.setText(names[index][0]); self.subtitle.setText(names[index][1])
        for i,btn in enumerate(self.nav): btn.setChecked(i==index)
        if refresh and index==0: self.poll()
        if refresh and index==5: self.refresh_network_status()
        if index==4:
            self.proxy.hide()
            self.plugin_detail.hide(); self.plugin_center.show()
            if refresh: self.refresh_plugins()

    def set_busy(self,value):
        self.busy=value
        for widget in self.guarded: widget.setEnabled(not value)

    def work(self,fn,callback,message='正在处理…',silent=False):
        if self.busy:
            if not silent: self.notify('设备正在执行其他操作，请稍后。')
            return
        if not silent: self.banner.hide()
        self.set_busy(True); self.activity.setText(message); self.job=Job(fn)
        self.job.signals.result.connect(lambda data:self.complete(callback,data,silent))
        self.job.signals.error.connect(lambda error:self.failed(error,silent)); self.pool.start(self.job)

    def complete(self,callback,data,silent):
        self.wifi_pending=False
        self.set_busy(False); self.activity.setText('就绪')
        try: callback(data)
        except Exception as exc: self.notify(str(exc),True)

    def failed(self,error,silent=False):
        self.set_busy(False); self.activity.setText('操作未完成')
        if self.wifi_pending: self.wifi_status.setText('操作未完成，请查看提示并刷新状态。')
        self.wifi_pending=False
        if silent:
            self.previous_cpu=None; self.set_badge(False,'连接中断'); self.activity.setText('设备状态已过期 · 请刷新连接')
        if self.stack.currentIndex()==4: self.plugin_center.render({'state':'error'})
        self.notify(error,True)

    def require_device(self):
        if not self.serial: self.notify('请先连接并选择一个在线设备。',True); return False
        return True

    def call(self,action,data=None):
        # Internal dispatch only; this is not an HTTP request.
        return self.api.dispatch('/api/'+action,{'serial':self.serial,**(data or {})})

    def set_badge(self,online,text):
        self.badge.setText('●  '+text); self.badge.setProperty('online',online); self.badge.style().unpolish(self.badge); self.badge.style().polish(self.badge)

    def refresh(self):
        self.work(lambda:self.call('devices'),lambda data:self.render_devices(data['devices']),'正在识别 ADB 设备…')

    def render_devices(self,devices):
        self.devices=devices; old=self.serial; self.device_select.blockSignals(True); self.device_select.clear(); self.device_select.addItem('请选择设备' if devices else '未发现设备 · 连接 USB 后刷新','')
        for device in devices:
            self.device_select.addItem(f'{device["transport"]}  ·  {device["serial"]}  ·  {"在线" if device["state"]=="device" else device["state"]}',device['serial'])
            if device['state']!='device': self.device_select.model().item(self.device_select.count()-1).setEnabled(False)
        online=[d['serial'] for d in devices if d['state']=='device']; chosen=old if old in online else next(iter(online),''); self.device_select.setCurrentIndex(max(0,self.device_select.findData(chosen))); self.device_select.blockSignals(False); self.select_device()
        usb=next((d['serial'] for d in devices if d['state']=='device' and d['transport']=='USB'), '')
        if usb: self.sync_lan_address(usb)
        if any(d['state']=='unauthorized' for d in devices): self.connection_hint.setText('设备未授权，请在设备端允许 USB 调试。')
        elif not devices: self.connection_hint.setText('未识别到 ADB 设备。请检查数据线、OTG 接口、驱动与板端 adbd 服务。')

    def select_device(self):
        selected=self.device_select.currentData() or ''
        if selected!=self.serial and any(term.is_active() for term in self.terminals):
            if not self.ask('切换设备','切换设备会关闭全部终端会话，是否继续？'):
                self.device_select.blockSignals(True); self.device_select.setCurrentIndex(max(0,self.device_select.findData(self.serial))); self.device_select.blockSignals(False); return
        if selected!=self.serial: self.serial=selected; self.reset_data()
        if self.serial:
            for term in self.terminals:
                if not term.is_active(): term.connect_device(self.api.adb.path,self.serial)
        self.set_badge(bool(self.serial),'已连接' if self.serial else '未连接')
        if self.serial:
            transport=next((d['transport'] for d in self.devices if d['serial']==self.serial),'ADB'); self.hero_tag.setText(transport+' 已连接   ·   Linux / Buildroot'); self.connection_hint.setText('所有操作仅针对当前选中的设备。'); QTimer.singleShot(0,self.poll)

    def reset_data(self):
        self.proxy.reset(); self.proxy.hide()
        self.traffic.reset()
        self.plugin_center.render({'state':'unknown' if self.serial else 'offline'})
        self.plugin_detail.hide(); self.plugin_center.show()
        if self.stack.currentIndex()==4: self.go(4,False)
        for term in self.terminals: term.disconnect_device(); term.clear_screen()
        self.terminal_status.setText('未连接终端')
        self.clear_wifi_selection(); self.wifi_iface.blockSignals(True); self.wifi_iface.clear(); self.wifi_iface.addItem('自动选择网卡',''); self.wifi_iface.blockSignals(False)
        self.previous_cpu=None; self.file_serial=''; self.rows=[]; self.table.setRowCount(0); self.file_hint.setText('打开目录后查看文件。')
        for metric in self.metrics: metric.value.setText('—'); metric.detail.setText('等待设备数据'); metric.bar.setValue(0)
        for value in self.info_labels.values(): value.setText('—')
        self.hostname.setText('正在连接…' if self.serial else '你好，泰山派。'); self.os_label.setText('连接设备，开始你的工作。'); self.hero_tag.setText('USB 即连即用   ·   Linux / Buildroot')
        self.log_output.clear(); self.service_output.clear(); self.monitor_label.setText('安装并启动流量监控插件后显示数据。'); self.clear_storage(); self.storage_box.addWidget(label('等待设备数据','subtle'))

    def toggle_network(self): self.net_panel.setVisible(not self.net_panel.isVisible())

    def sync_lan_address(self,serial):
        if not self.api.adb.path or not Path(self.api.adb.path).is_file(): return
        def update(data):
            text=data.decode('utf-8','replace'); match=re.search(r'inet[ \t]+([0-9.]+)/',text)
            if not match: return
            ip=match.group(1); line=next((line for line in text.splitlines() if match.group(0) in line),''); interface=(line.split()[1] if len(line.split())>1 else '')
            if interface == 'lo' or ip.startswith(('127.','169.254.')): return
            self.address.setText(ip+':5555')
            self.address.setToolTip(f'从 USB ADB 自动读取：{interface} · {ip}')
            self.connection_hint.setText(f'已读取局域网地址 {ip}:5555；点击“无线连接”即可切换到网络 ADB。')
        self.work(lambda:self.api.adb.shell(serial,"ip -o -4 addr show scope global 2>/dev/null",timeout=5)[0],update,'正在读取泰山派局域网 IP…',True)

    def connect_network(self):
        address=self.address.text().strip()
        def connected(data):
            if self.serial!=address: self.serial=address; self.reset_data()
            self.render_devices(data['devices']); self.notify(data['message'])
        self.work(lambda:self.call('connect',{'address':address}),connected,'正在连接网络 ADB…')

    def poll(self):
        if self.serial and not self.busy and self.stack.currentIndex()==0 and not self.isMinimized(): self.work(lambda:self.call('status'),self.render_status,'正在更新状态…',True)

    def clear_storage(self):
        while self.storage_box.count():
            item=self.storage_box.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def render_status(self,data):
        self.set_badge(True,'设备在线'); self.hostname.setText(data['hostname']); self.os_label.setText(data['os']); self.activity.setText('更新于 '+time.strftime('%H:%M:%S'))
        self.info_labels['serial'].setText(self.serial); self.info_labels['kernel'].setText(data['kernel']); self.info_labels['network'].setText(data['network'] or '未获取到 IPv4 地址')
        cpu,mem,temp,uptime=self.metrics
        if self.previous_cpu:
            total=data['cpuTotal']-self.previous_cpu[0]; idle=data['cpuIdle']-self.previous_cpu[1]
            if total>0:
                usage=min(100,max(0,100*(1-idle/total))); cpu.value.setText(f'{usage:.1f}%'); cpu.bar.setValue(round(usage*10))
        else: cpu.value.setText('采样中')
        self.previous_cpu=(data['cpuTotal'],data['cpuIdle']); cpu.detail.setText('每 5 秒更新')
        ratio=100*data['memoryUsed']/data['memoryTotal'] if data['memoryTotal'] else 0; mem.value.setText(f'{ratio:.1f}%'); mem.bar.setValue(round(ratio*10)); mem.detail.setText(bytes_text(data['memoryUsed'])+' / '+bytes_text(data['memoryTotal']))
        temp.value.setText(f'{data["temperature"]:.1f}°' if data['temperature'] is not None else '不支持'); temp.bar.setValue(min(1000,max(0,int((data['temperature'] or 0)*10)))); temp.detail.setText('摄氏度 · thermal_zone0')
        minutes=int(data['uptime']/60); uptime.value.setText(f'{minutes//1440}天 {minutes%1440//60}时' if minutes>=1440 else f'{minutes//60}时 {minutes%60}分'); uptime.bar.setValue(0); uptime.detail.setText('负载 '+' / '.join(data['load']))
        self.monitor_label.setText('已检测到监控服务，流量面板会检查插件版本。' if data['monitorAvailable'] else '此设备未安装监控插件，可在“插件中心”安装。')
        self.clear_storage()
        for disk in data['disks']:
            group=QWidget(); layout=QVBoxLayout(group); layout.setContentsMargins(0,0,0,5); layout.setSpacing(7); row=QHBoxLayout(); row.addWidget(label(disk['mount'],'infoValue')); row.addStretch(); row.addWidget(label(disk['percent'],'caption')); layout.addLayout(row)
            bar=QProgressBar(); bar.setRange(0,100); bar.setValue(int(disk['percent'].rstrip('%'))); bar.setTextVisible(False); bar.setFixedHeight(5); layout.addWidget(bar); layout.addWidget(label(bytes_text(disk['used'])+' / '+bytes_text(disk['total']),'caption')); self.storage_box.addWidget(group)

    def load_files(self):
        if not self.require_device(): return
        path=self.path_edit.text()
        self.work(lambda:self.call('files',{'path':path}),self.render_files,'正在读取目录…')

    def render_files(self,data):
        self.rows=data['files']; self.current_dir=data['path']; self.file_serial=self.serial; self.path_edit.setText(self.current_dir); self.table.setRowCount(len(self.rows))
        for row,file in enumerate(self.rows):
            name=QTableWidgetItem(file['name']); name.setIcon(icon('folder' if file['type']=='d' else 'file','#007AFF' if file['type']=='d' else '#8e8e93',20)); name.setData(Qt.ItemDataRole.UserRole,file)
            self.table.setItem(row,0,name); self.table.setItem(row,1,QTableWidgetItem('—' if file['type']=='d' else bytes_text(file['size']))); self.table.setItem(row,2,QTableWidgetItem(time.strftime('%Y-%m-%d %H:%M',time.localtime(file['modified']))))
        self.file_hint.setText(f'{len(self.rows)} 个项目'+(' · 仅显示前 1000 项' if data['limited'] else ' · 双击文件夹进入，单个文件传输上限 512 MB'))

    def parent_dir(self): self.path_edit.setText(str(Path(self.current_dir).parent).replace('\\','/') or '/'); self.load_files()

    def open_entry(self,row,column):
        if self.busy or not 0<=row<len(self.rows): return
        entry=self.rows[row]
        if entry['type']=='d': self.path_edit.setText(self.current_dir.rstrip('/')+'/'+entry['name']); self.load_files()

    def ask(self,title,text):
        dialog=QMessageBox(self); dialog.setWindowTitle(title); dialog.setText(title); dialog.setInformativeText(text); dialog.setIcon(QMessageBox.Icon.Question)
        yes=dialog.addButton('确认',QMessageBox.ButtonRole.AcceptRole); no=dialog.addButton('取消',QMessageBox.ButtonRole.RejectRole); dialog.setDefaultButton(no); dialog.exec(); return dialog.clickedButton()==yes

    def upload(self):
        if not self.require_device(): return
        if self.file_serial!=self.serial: self.notify('请先打开目标目录。',True); return
        filename,_=QFileDialog.getOpenFileName(self,'选择上传文件')
        if not filename: return
        source=Path(filename); dest=self.current_dir.rstrip('/')+'/'+source.name
        if source.stat().st_size>MAX_TRANSFER: self.notify('单个文件上限为 512 MB。',True); return
        if not self.ask('上传文件',f'目标：{dest}\n同名文件将被覆盖。'): return
        serial=self.serial
        def transfer():
            target=remote_path(dest)
            with self.api.adb.lock(serial):
                self.api.adb.run(['-s',serial,'push',str(source),target],timeout=180)
            return target
        self.work(transfer,lambda result:(self.notify('上传完成：'+result),self.load_files()),'正在上传，请等待…')

    def download(self):
        if not self.require_device(): return
        row=self.table.currentRow()
        if self.file_serial!=self.serial or row<0 or row>=len(self.rows): self.notify('请先选择一个文件。',True); return
        entry=self.rows[row]
        if entry['type']=='d': self.notify('请选择文件，暂不支持目录下载。',True); return
        source=remote_path(self.current_dir.rstrip('/')+'/'+entry['name'])
        filename,_=QFileDialog.getSaveFileName(self,'保存文件',entry['name'])
        if not filename: return
        serial=self.serial
        def transfer():
            with self.api.adb.lock(serial):
                raw,_,_=self.api.adb.shell(serial,'test -f '+shlex.quote(source)+' && stat -Lc %s '+shlex.quote(source))
                if int(raw.strip())>MAX_TRANSFER: raise UserError('单个下载文件上限为 512 MB。')
                # Download beside destination, then atomically replace after success.
                dest=Path(filename)
                with tempfile.TemporaryDirectory(prefix='.tspi-',dir=dest.parent) as tmp:
                    stage=Path(tmp)/'payload'; self.api.adb.run(['-s',serial,'pull',source,str(stage)],timeout=180); os.replace(stage,dest)
            return filename
        self.work(transfer,lambda result:self.notify('已保存：'+result),'正在下载，请等待…')

    def logs(self):
        if self.require_device():
            kind=self.log_kind.currentData(); self.work(lambda:self.call('logs',{'kind':kind}),lambda data:self.log_output.setPlainText(data['output'] or '暂无日志'),'正在读取日志…')

    def save_logs(self):
        filename,_=QFileDialog.getSaveFileName(self,'保存日志','taishanpi.log','日志文件 (*.log);;文本文件 (*.txt)')
        if filename:
            try: Path(filename).write_text(self.log_output.toPlainText(),encoding='utf-8'); self.notify('已保存：'+filename)
            except OSError as exc: self.notify(str(exc),True)

    def service(self,action):
        if not self.require_device(): return
        if action!='status' and not self.ask('确认服务操作',f'将在 {self.serial} 上'+('启动' if action=='start' else '停止')+'监控服务。'): return
        def completed(data):
            self.service_output.setPlainText(data['output'] or '操作完成')
            self.plugin_center.render({'state':'unknown'})
            QTimer.singleShot(0,self.refresh_plugins)
        self.work(lambda:self.call('service',{'action':action,'confirm':True}),completed,'正在执行服务操作…')

    def install_monitor(self):
        if not self.require_device() or self.busy: return
        autostart=self.monitor_autostart.isChecked()
        detail='将在当前设备 /userdata/bin 安装监控脚本，先校验依赖并备份已有插件文件。\n新插件仅统计网卡实时速度和累计流量，不再执行健康检查。\n'
        detail+=('同时配置开机自动启动。' if autostart else '不更改已有开机启动设置。')
        detail+='\n安装完成后可点击“启动服务”。'
        if not self.ask('安装监控插件',detail): return
        def completed(data):
            self.monitor_label.setText('监控插件已安装。可查看状态或启动服务。')
            self.service_output.setPlainText(data['output']); self.notify('监控插件安装成功。')
            self.plugin_center.render({'state':'stopped'})
        self.work(lambda:self.call('monitor-install',{'confirm':True,'autostart':autostart}),completed,'正在上传、校验并安装监控插件…')

    def uninstall_monitor(self):
        if not self.require_device() or self.busy: return
        if self.traffic.speed_busy:
            self.notify('测速正在进行，请等待完成后卸载插件。',True); return
        if not self.ask('卸载网络流量插件',f'将从设备 {self.serial} 卸载网络流量插件。\n\n停止监控服务，移除开机启动与插件程序。\n累计流量、日志和原有备份保留；重新安装后可以继续统计。\n\n确认卸载？'): return
        def completed(data):
            self.traffic.reset(); self.traffic.render({'state':'missing'})
            self.plugin_center.render({'state':'missing'})
            self.monitor_label.setText('插件已卸载，累计流量和日志已保留。')
            self.service_output.setPlainText(data['output']); self.monitor_autostart.setChecked(False)
            self.notify('网络流量插件已卸载，累计流量已保留。')
        self.work(lambda:self.call('monitor-uninstall',{'confirm':True}),completed,'正在停止服务并卸载插件…')

    def reboot(self):
        if not self.require_device() or not self.ask('重启设备',f'确认重启 {self.serial}？\n当前运行的服务将中断。'): return
        self.work(lambda:self.call('reboot',{'confirm':True}),lambda data:(self.notify(data['message']),self.set_badge(False,'正在重启')),'正在发送重启指令…')

    def closeEvent(self,event):
        if self.busy or self.pool.activeThreadCount() or self.traffic.pool.activeThreadCount():
            self.notify('操作仍在进行，请等待结束后关闭窗口。',True); event.ignore(); return
        if any(term.is_active() for term in self.terminals) and not self.ask('退出软件','全部终端会话将关闭，是否退出？'):
            event.ignore(); return
        for term in self.terminals: term.disconnect_device()
        self.timer.stop(); self.traffic.timer.stop(); event.accept()


STYLE='''
QWidget { font-family: "Segoe UI", "Microsoft YaHei UI"; font-size: 13px; color: #1c1c1e; }
QWidget#root, QWidget#page, QScrollArea { background: #f5f5f7; }
QFrame#sidebar { background: #edf0f5; border-right: 1px solid #e0e3e9; }
QLabel { background: transparent; }
QLabel#brand { font-size: 20px; font-weight: 700; }
QLabel#title { font-size: 30px; font-weight: 700; letter-spacing: -1px; }
QLabel#subtle { color: #86868b; font-size: 13px; }
QLabel#caption { color: #929298; font-size: 11px; }
QLabel#sideHeading { color: #94949c; font-size: 11px; font-weight: 600; }
QLabel#sideStatus { color: #67807b; font-size: 11px; }
QLabel#section { font-size: 16px; font-weight: 600; }
QLabel#eyebrow { color: #7394ba; font-size: 10px; font-weight: 600; letter-spacing: 2px; }
QLabel#heroTitle { color: #1b3354; font-size: 29px; font-weight: 700; }
QLabel#heroSub { color: #70869f; font-size: 13px; }
QLabel#heroCaption { color: #6389bc; font-size: 11px; }
QLabel#metricValue { font-size: 27px; font-weight: 600; letter-spacing: -1px; }
QLabel#infoValue { font-size: 12px; color: #5e606a; }
QFrame#card { background: #ffffff; border: 1px solid #eceef2; border-radius: 18px; }
QFrame#hero { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #e8f2ff,stop:1 #eef0fc); border: 1px solid #e1e9f6; border-radius: 22px; }
QLabel#badge { border-radius: 13px; background: #e9e9ee; color: #8c8c93; padding: 7px 13px; font-size: 11px; }
QLabel#badge[online="true"] { color: #248652; background: #e5f5ec; }
QLabel#notice { background: #e6efff; border-radius: 10px; padding: 12px 16px; color: #2865b4; font-size: 12px; }
QLabel#notice[error="true"] { background: #fff0ed; color: #ae564a; }
QPushButton { background: #edf3fd; color: #007aff; padding: 8px 15px; border: none; border-radius: 10px; font-weight: 500; }
QPushButton:hover { background: #e0ebfd; }
QPushButton:pressed { background: #d4e3fc; }
QPushButton[kind="primary"] { background: #007aff; color: white; }
QPushButton[kind="primary"]:hover { background: #006ce3; }
QPushButton[kind="danger"] { background: #ffefed; color: #e05248; }
QPushButton[kind="filter"]:checked { background: #007aff; color: white; }
QPushButton[kind="nav"] { text-align: left; background: transparent; color: #626571; padding-left: 14px; font-size: 13px; border-radius: 12px; }
QPushButton[kind="nav"]:hover { background: #e1e7f0; }
QPushButton[kind="nav"]:checked { background: #dceaff; color: #0069de; font-weight: 600; }
QPushButton:disabled { background: #f0f0f3; color: #b6b6bd; }
QLineEdit, QComboBox { min-height: 21px; padding: 8px 12px; background: #f5f6f9; border: 1px solid #eceef3; border-radius: 9px; selection-background-color: #007aff; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #80b8ff; }
QComboBox::drop-down { border: none; width: 25px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #e1edff; selection-color: #0064d8; border: 1px solid #e5e5ec; outline: none; padding: 4px; }
QPlainTextEdit { background: #f7f8fb; border: 1px solid #e9ecf2; border-radius: 12px; padding: 12px; font-family: "Cascadia Code", "Consolas", "Microsoft YaHei UI"; font-size: 12px; selection-background-color: #cce3ff; }
QPlainTextEdit#console { background: #f7f8fb; color: #44526a; }
QProgressBar { background: #eef0f5; border: none; border-radius: 2px; }
QProgressBar::chunk { background: #65a5ff; border-radius: 2px; }
QTableWidget { border: none; background: white; gridline-color: #f0f1f5; selection-background-color: #edf4ff; selection-color: #0065d6; outline: none; }
QHeaderView { background: #f6f7fa; }
QHeaderView::section { background: #f6f7fa; padding: 12px; border: none; color: #8e8e95; font-size: 11px; font-weight: 500; }
QTableWidget::item { padding: 6px; border-bottom: 1px solid #f2f3f6; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 2px; }
QScrollBar::handle:vertical { background: #cdd1da; border-radius: 3px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QCheckBox { color: #73737d; font-size: 12px; spacing: 7px; }
QToolTip { background: #fff; border: 1px solid #dedee5; padding: 8px; color: #444; }
QMessageBox { background: #f5f5f7; }
'''


DARK_STYLE = '''
QWidget { color: #f2f2f7; }
QWidget#root, QWidget#page, QScrollArea, QMessageBox { background: #161619; }
QFrame#sidebar { background: #1c1c20; border-right: 1px solid #303036; }
QLabel#subtle, QLabel#infoValue { color: #b1b1bb; }
QLabel#caption, QLabel#sideHeading { color: #95959f; }
QLabel#sideStatus { color: #90b9ac; }
QLabel#eyebrow, QLabel#heroCaption { color: #91b6e8; }
QLabel#heroTitle { color: #e2edff; }
QLabel#heroSub { color: #a6bad7; }
QFrame#card { background: #242428; border-color: #333339; }
QFrame#hero { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1d304c,stop:1 #242a42); border-color: #31415a; }
QLabel#badge { background: #2c2c32; color: #b1b1bb; }
QLabel#badge[online="true"] { background: #19382a; color: #77d6a0; }
QLabel#notice { background: #21334e; color: #acd0ff; }
QLabel#notice[error="true"] { background: #452c2b; color: #ffa99e; }
QPushButton { background: #263449; color: #69adff; }
QPushButton:hover { background: #304360; }
QPushButton:pressed { background: #395477; }
QPushButton[kind="primary"] { background: #0a64ce; color: white; }
QPushButton[kind="primary"]:hover { background: #1676e3; }
QPushButton[kind="danger"] { background: #412b2c; color: #ff9c94; }
QPushButton[kind="filter"]:checked { background: #007aff; color: white; }
QPushButton[kind="nav"] { background: transparent; color: #b4b4bf; }
QPushButton[kind="nav"]:hover { background: #292d36; }
QPushButton[kind="nav"]:checked { background: #233956; color: #78b5ff; }
QPushButton:disabled { background: #29292e; color: #71717b; }
QLineEdit, QComboBox { background: #1c1c20; border-color: #3b3b43; selection-background-color: #0a64ce; selection-color: white; }
QLineEdit:focus, QComboBox:focus { border-color: #599eea; }
QComboBox QAbstractItemView { background: #29292e; color: #f2f2f7; selection-background-color: #233f63; selection-color: #c4dfff; border-color: #44444c; }
QPlainTextEdit, QPlainTextEdit#console { background: #1b1c21; color: #d0d8e5; border-color: #35353f; selection-background-color: #234875; selection-color: white; }
QProgressBar { background: #35353e; }
QProgressBar::chunk { background: #64aaff; }
QTableWidget { background: #242428; color: #e3e3eb; gridline-color: #383840; selection-background-color: #233f63; selection-color: #c4dfff; }
QHeaderView { background: #2c2c32; }
QHeaderView::section { background: #2c2c32; color: #b1b1bb; }
QTableWidget::item { border-bottom-color: #34343b; }
QScrollBar::handle:vertical { background: #50505c; }
QCheckBox { color: #b9b9c4; }
QToolTip { background: #303038; border-color: #50505c; color: #f2f2f7; }
'''


def main():
    app=QApplication(sys.argv); app.setApplicationName('泰山派设备管理'); configure_app(app); app.setWindowIcon(app_icon())
    window=Window(); window.show()
    if '--smoke-test' in sys.argv:
        attempts=[0]
        def verify():
            attempts[0]+=1
            if window.busy and attempts[0]<40: QTimer.singleShot(500,verify); return
            ok=(not window.busy and Path(window.api.adb.path).is_file() and window.stack.count()==6 and window.device_select.count()>0 and not window.banner.property('error'))
            from monitor_plugin import ASSETS, NAMES
            ok = ok and all((ASSETS/name).is_file() for name in (*NAMES,'S95check-monitor'))
            from adb_core import PORTABLE
            ok = ok and all((PORTABLE/'iperf3'/name).is_file() for name in ('iperf3.exe','cygwin1.dll'))
            ok = ok and hasattr(window,'traffic') and len(window.traffic.values)==4 and len(window.plugin_center.cards)==6
            from proxy_plugin import ASSETS as PROXY_ASSETS
            ok = ok and (PROXY_ASSETS/'mihomo.gz').is_file()
            previous = app.property('theme')
            for theme in ('dark', 'light'):
                apply_theme(app, theme)
                ok = ok and app.property('theme') == theme and window.theme_btn.text() == ('浅色模式' if theme == 'dark' else '深色模式')
            apply_theme(app, previous)
            app.exit(0 if ok else 2)
        QTimer.singleShot(3000,verify)
    sys.exit(app.exec())


if __name__=='__main__': main()
