"""Native network proxy panel; no browser dashboard or credentials in settings."""
from PySide6.QtCore import QTimer
from selection_widgets import QComboBox
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView


class ProxyPanel(QWidget):
    def __init__(self, owner, label, button, card):
        super().__init__(); self.owner=owner; self.controls=[]; self.groups=[]
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(16)
        row=QHBoxLayout(); row.addWidget(button('‹ 返回插件中心',owner.close_plugin)); row.addStretch(); layout.addLayout(row)
        frame,box=card(); box.addWidget(label('网络代理','title')); box.addWidget(label('Mihomo 1.19.31 · Clash.Meta 兼容核心 · ARM64','subtle'))
        self.state=label('连接设备后查看状态。','infoValue',True); box.addWidget(self.state)
        row=QHBoxLayout()
        for title,action in [('安装 / 升级核心','install'),('刷新状态','status'),('启动','start'),('停止','stop'),('重启','restart')]:
            btn=button(title,lambda checked=False,a=action:self.action(a),'primary' if action=='install' else 'secondary'); row.addWidget(btn); self.controls.append(btn)
        box.addLayout(row)
        row=QHBoxLayout(); self.autostart=QCheckBox('开机自动启动'); self.autostart.clicked.connect(lambda checked:self.action('enable' if checked else 'disable')); row.addWidget(self.autostart); self.controls.append(self.autostart)
        row.addStretch(); btn=button('卸载插件',lambda:self.action('uninstall'),'danger'); row.addWidget(btn); self.controls.append(btn); box.addLayout(row); layout.addWidget(frame)
        frame,box=card(); box.addWidget(label('代理配置','section'))
        box.addWidget(label('导入 Clash/Mihomo YAML 或 JSON 配置。配置保存在泰山派，HTTP 与 SOCKS 共用 7890 端口。','subtle',True))
        row=QHBoxLayout(); self.allow_lan=QCheckBox('允许局域网设备使用代理'); row.addWidget(self.allow_lan); self.controls.append(self.allow_lan)
        row.addStretch(); btn=button('导入配置文件',self.import_file,'primary'); row.addWidget(btn); self.controls.append(btn); box.addLayout(row)
        box.addWidget(label('默认只允许泰山派本机使用。启用局域网访问时，其他设备需手动设置代理地址为泰山派 IP:7890。','caption',True))
        box.addWidget(label('此版本使用显式代理：不启用 TUN、透明代理或 DNS 接管。订阅请先下载为 Clash 配置文件再导入。','caption',True)); layout.addWidget(frame)
        frame,box=card(); row=QHBoxLayout(); row.addWidget(label('模式与节点','section')); row.addStretch()
        self.mode=QComboBox()
        for title,value in [('规则','rule'),('全局','global'),('直连','direct')]: self.mode.addItem(title,value)
        row.addWidget(self.mode); self.controls.append(self.mode)
        btn=button('应用模式',lambda:self.action('mode',{'mode':self.mode.currentData()})); row.addWidget(btn); self.controls.append(btn)
        btn=button('刷新策略组',lambda:self.action('groups')); row.addWidget(btn); self.controls.append(btn); box.addLayout(row)
        self.table=QTableWidget(0,3); self.table.setHorizontalHeaderLabels(['策略组','类型','当前节点']); self.table.verticalHeader().hide(); self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection); self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.table.setMinimumHeight(160); self.table.itemSelectionChanged.connect(self.choose_group); box.addWidget(self.table); self.controls.append(self.table)
        row=QHBoxLayout(); self.nodes=QComboBox(); row.addWidget(self.nodes,1); self.controls.append(self.nodes)
        btn=button('切换节点',self.select_node,'primary'); row.addWidget(btn); self.controls.append(btn); box.addLayout(row); layout.addWidget(frame)
        self.result=label('先安装核心，再导入配置并启动。','notice',True); layout.addWidget(self.result)

    def reset(self):
        self.groups=[]; self.table.setRowCount(0); self.nodes.clear(); self.autostart.setChecked(False); self.allow_lan.setChecked(False); self.mode.setCurrentIndex(0)
        self.state.setText('连接设备后查看状态。'); self.result.setText('先安装核心，再导入配置并启动。')

    def render(self,data):
        if 'state' in data:
            state=data['state']; titles={'missing':'未安装','running':'运行中','stopped':'已停止','error':'检测失败','offline':'未连接'}
            self.state.setText(titles.get(state,'待检测')+' · '+('已有配置' if data.get('configured') else '尚未导入配置'))
            self.autostart.setChecked(data.get('autostart',False))
            self.owner.plugin_center.render_proxy(data)
            if state!='running': self.groups=[]; self.table.setRowCount(0); self.nodes.clear()
        if 'output' in data: self.result.setText(data['output'])
        if 'groups' in data:
            self.groups=data['groups']; self.table.setRowCount(len(self.groups))
            for r,group in enumerate(self.groups):
                for c,key in enumerate(('name','type','now')):
                    item=QTableWidgetItem(str(group.get(key,''))); item.setToolTip(item.text()); self.table.setItem(r,c,item)
            self.mode.setCurrentIndex(max(0,self.mode.findData(data.get('mode','rule'))))
            if self.groups: self.table.selectRow(0)
            self.result.setText('选择策略组，再选择节点。自动测速/故障转移组由核心按配置选择节点。')

    def action(self,action,data=None):
        owner=self.owner
        if not owner.require_device() or owner.busy: return
        if action=='install' and not owner.ask('安装网络代理','将安装官方 Mihomo ARM64 核心及服务脚本到 /userdata/network-proxy。安装后需要导入配置，不会自动启动代理。'): return
        if action=='uninstall' and not owner.ask('卸载网络代理','将停止代理并移除开机启动入口。配置与核心保留在设备备份目录。'): return
        if action=='install': data={'confirm':True}
        serial=owner.serial
        def done(result):
            self.render(result)
            if action in ('start','restart','select','mode'):
                QTimer.singleShot(0,lambda:self.action('groups') if owner.serial==serial and self.isVisible() else None)
        owner.work(lambda:owner.api.dispatch('/api/proxy-'+action,{'serial':serial,**(data or {})}),done,'正在处理网络代理，请稍候…')

    def import_file(self):
        if not self.owner.require_device() or self.owner.busy: return
        name,_=QFileDialog.getOpenFileName(self,'导入 Clash/Mihomo 配置','','代理配置 (*.yaml *.yml *.json);;所有文件 (*)')
        if name: self.action('import',{'filename':name,'allow_lan':self.allow_lan.isChecked()})

    def choose_group(self):
        self.nodes.clear(); r=self.table.currentRow()
        if not 0<=r<len(self.groups): return
        group=self.groups[r]; self.nodes.addItems(group.get('all',[])); self.nodes.setCurrentText(group.get('now',''))

    def select_node(self):
        r=self.table.currentRow()
        if not 0<=r<len(self.groups) or not self.nodes.currentText(): return
        if self.groups[r].get('type')!='Selector': self.result.setText('此组由核心自动选择，请选择 Selector 类型的手动策略组。'); return
        self.action('select',{'group':self.groups[r]['name'],'node':self.nodes.currentText()})
