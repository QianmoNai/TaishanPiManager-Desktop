"""Native plugin catalog; every entry maps to a real bundled capability."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit, QButtonGroup


class PluginCenter(QWidget):
    def __init__(self, owner, label, button, card, icon):
        super().__init__(); self.owner=owner; self.state='unknown'; self.cards=[]
        self.category='全部'; self.detail_callback=None; self.proxy_state='unknown'
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(18)
        hero,box=card(); hero.setObjectName('hero'); box.setContentsMargins(26,23,26,23)
        row=QHBoxLayout(); intro=QVBoxLayout(); intro.addWidget(label('TAISHANPI  /  PLUGINS','eyebrow'))
        intro.addWidget(label('为你的泰山派，添加更多可能。','heroTitle',True))
        intro.addWidget(label('发现工具，管理插件，让常用功能各就其位。','heroSub',True)); row.addLayout(intro,1)
        art=label(); art.setPixmap(icon('settings','#007aff',58).pixmap(58,58)); row.addWidget(art); box.addLayout(row)
        self.summary=label('2 款可安装插件 · 5 项内置工具','heroCaption'); box.addWidget(self.summary); layout.addWidget(hero)
        row=QHBoxLayout(); self.search=QLineEdit(); self.search.setPlaceholderText('搜索插件或工具'); self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName('搜索插件'); self.search.textChanged.connect(self.filter_cards); row.addWidget(self.search,1)
        self.refresh=button('刷新插件状态',owner.refresh_plugins,symbol='refresh'); row.addWidget(self.refresh); layout.addLayout(row)
        row=QHBoxLayout(); self.tabs=QButtonGroup(self); self.tabs.setExclusive(True)
        for title in ('全部','已安装','网络','系统'):
            btn=button(title,lambda checked=False,t=title:self.set_category(t)); btn.setCheckable(True); btn.setProperty('kind','filter')
            btn.setChecked(title=='全部'); self.tabs.addButton(btn); row.addWidget(btn)
        row.addStretch(); layout.addLayout(row)
        self.hint=label('连接设备后可查看插件安装与运行状态。','subtle',True); layout.addWidget(self.hint)
        self.grid=QGridLayout(); self.grid.setSpacing(14); self.grid.setColumnStretch(0,1); self.grid.setColumnStretch(1,1); layout.addLayout(self.grid)
        items=[
            ('traffic','网络流量','实时速度、双向测速与累计流量，清晰掌握每一次收发。','网络','wifi','#007aff','v2.0 · 随软件提供',None),
            ('proxy','网络代理','Mihomo 核心安装、配置导入、规则分流与策略组切换。','网络','usb','#8b5cf6','Mihomo 1.19.31 · ARM64',None),
            ('serial','串口助手','UART3 排针串口参数、文本/HEX 收发、VOFA 协议波形与引脚配置。','系统','usb','#e5a13d','内置工具 · ADB 串口会话',None),
            ('wifi','网络设置','管理 Wi-Fi、USB 网卡与网络路由。','网络','wifi','#30b86b','内置工具 · 无需安装',5),
            ('terminal','交互终端','持续 Shell 会话，彩色输出、补全与快捷操作。','系统','terminal','#8b5cf6','内置工具 · 无需安装',3),
            ('files','文件管理','浏览设备目录，在电脑与泰山派之间传输文件。','系统','folder','#ee9a24','内置工具 · 无需安装',1),
            ('logs','系统日志','查看内核、系统和监控日志，定位运行问题。','系统','logs','#e36c86','内置工具 · 无需安装',2),
        ]
        for key,title,description,category,symbol,color,meta,page in items:
            frame,box=card(); box.setSpacing(10); frame.setMinimumHeight(205)
            row=QHBoxLayout(); tile=label(); tile.setPixmap(icon(symbol,color,34).pixmap(34,34)); row.addWidget(tile)
            titles=QVBoxLayout(); titles.setSpacing(3); titles.addWidget(label(title,'section')); titles.addWidget(label(meta,'caption')); row.addLayout(titles,1)
            badge=label('待检测' if key in ('traffic','proxy') else '内置','badge'); row.addWidget(badge); box.addLayout(row)
            box.addWidget(label(description,'subtle',True)); box.addStretch()
            row=QHBoxLayout(); row.addWidget(label(category+' · '+('板端插件' if key in ('traffic','proxy') else '桌面工具'),'caption')); row.addStretch()
            open_btn=button('查看详情' if key in ('traffic','proxy') else '打开',lambda checked=False,p=page,k=key:owner.open_serial() if k=='serial' else owner.open_proxy() if k=='proxy' else self.open_traffic() if p is None else owner.go(p),'primary' if key in ('traffic','proxy') else 'secondary')
            row.addWidget(open_btn); box.addLayout(row)
            if key=='traffic': self.traffic_badge=badge; self.traffic_open=open_btn
            if key=='proxy': self.proxy_badge=badge
            self.cards.append({'key':key,'name':title,'description':description,'category':category,'widget':frame})
        self.empty=label('没有找到匹配的插件。试试其他关键词或分类。','subtle',True); self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter); self.empty.setMinimumHeight(100); layout.addWidget(self.empty)
        footer=label('本地插件目录 · 当前提供 2 款可安装插件，其余为内置工具快捷入口。','caption',True); layout.addWidget(footer)
        frame,box=card(); row=QHBoxLayout(); row.addWidget(label('设备维护','section')); row.addStretch()
        owner.reboot_btn=button('重启设备',owner.reboot,'danger','power'); row.addWidget(owner.reboot_btn); box.addLayout(row)
        box.addWidget(label('重启会中断服务与 ADB 连接。固件烧录请使用瑞芯微烧录工具。','caption',True)); layout.addWidget(frame)
        self.filter_cards()

    def open_traffic(self):
        if self.detail_callback: self.detail_callback()

    def set_category(self,title):
        self.category=title; self.filter_cards()

    def filter_cards(self,*args):
        while self.grid.count(): self.grid.takeAt(0)
        query=self.search.text().strip().casefold(); visible=0
        for entry in self.cards:
            installed=entry['key']!='traffic' or self.state in ('running','stopped','update','stale')
            if entry['key']=='proxy': installed=self.proxy_state in ('running','stopped')
            matches=(not query or query in (entry['name']+' '+entry['description']+' '+entry['key']).casefold())
            matches=matches and (self.category=='全部' or self.category==entry['category'] or (self.category=='已安装' and installed))
            entry['widget'].setVisible(matches)
            if matches:
                self.grid.addWidget(entry['widget'],visible//2,visible%2); visible+=1
        self.empty.setVisible(visible==0)

    def render(self,data):
        if 'proxy' in data: self.render_proxy(data['proxy'])
        elif data.get('state') in ('offline','unknown'): self.render_proxy({'state':data['state']})
        self.state=data.get('state','unknown')
        titles={'running':'运行中','stopped':'已安装 · 已停止','missing':'未安装','update':'可更新','stale':'采样异常','unknown':'待检测','offline':'未连接','error':'检测失败'}
        self.traffic_badge.setText(titles.get(self.state,'待检测'))
        self.traffic_badge.setProperty('online',self.state=='running'); self.traffic_badge.style().unpolish(self.traffic_badge); self.traffic_badge.style().polish(self.traffic_badge)
        self.traffic_open.setText('打开' if self.state in ('running','stopped','stale') else '查看详情')
        if hasattr(self.owner,'plugin_primary'):
            self.owner.plugin_primary.setText({'missing':'安装插件','update':'升级插件','stopped':'启动服务','running':'服务管理'}.get(self.state,'查看状态'))
        if hasattr(self.owner,'uninstall_monitor_btn'):
            self.owner.uninstall_monitor_btn.setVisible(self.state in ('running','stopped','update','stale','unknown','error'))
        messages={'running':'网络流量插件正在运行，可点击“打开”查看实时数据。',
            'stopped':'网络流量插件已安装，打开详情后可启动服务。',
            'missing':'网络流量插件尚未安装，打开详情即可离线安装。',
            'update':'设备插件与随包版本不同，请在详情中查看并按需升级。',
            'unknown':'连接设备后可查看插件安装与运行状态。',
            'offline':'未选择在线设备。可以浏览插件与工具介绍。',
            'error':'插件状态检测失败，请检查设备连接后刷新。'}
        self.hint.setText(messages.get(self.state,'插件采样异常，请进入详情检查服务状态。'))
        self.filter_cards()

    def render_proxy(self,data):
        self.proxy_state=data.get('state','unknown')
        self.proxy_badge.setText({'running':'运行中','stopped':'已安装','missing':'未安装','offline':'未连接','error':'检测失败'}.get(self.proxy_state,'待检测'))
        self.filter_cards()
