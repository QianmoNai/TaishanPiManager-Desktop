"""Responsive node cards with measured Mihomo connectivity and cancellable batches."""
from PySide6.QtCore import QTimer, Qt, Signal
from selection_widgets import QComboBox
from proxy_diagnostics_widget import DiagnosticsPanel
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QCheckBox, QFileDialog, QLineEdit, QPushButton, QSizePolicy, QLabel


class NodeCard(QPushButton):
    chosen=Signal(str)
    probe=Signal(str)

    def __init__(self,name,meta,selected,result,label,button,parent=None):
        super().__init__(parent); self.name=name; self.setObjectName('proxyNode'); self.setProperty('selected',selected)
        self.setCursor(Qt.CursorShape.PointingHandCursor); self.setMinimumWidth(0); self.setFixedHeight(94)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        self.setAccessibleName(name+('，当前节点' if selected else '')); self.clicked.connect(lambda:self.chosen.emit(name))
        box=QVBoxLayout(self); box.setContentsMargins(13,10,13,9); box.setSpacing(7)
        self.title=label(name,'nodeTitle'); self.title.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred); self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents); box.addWidget(self.title)
        row=QHBoxLayout(); row.setSpacing(6)
        tags=str(meta.get('type','Unknown'))+(' · UDP' if meta.get('udp') else '')
        self.tags=label(tags,'caption'); self.tags.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred); self.tags.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents); row.addWidget(self.tags,1)
        self.delay=label('','nodeDelay'); self.delay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents); row.addWidget(self.delay)
        test=button('测试',lambda:self.probe.emit(name)); test.setObjectName('nodeTest'); test.setMinimumHeight(24); test.setMaximumHeight(26); test.setFixedWidth(45); row.addWidget(test); box.addLayout(row)
        self.update_node(meta,selected,result)

    def update_node(self,meta,selected,result):
        self.setProperty('selected',selected)
        self.setAccessibleName(self.name+('，当前节点' if selected else ''))
        tags=str(meta.get('type','Unknown'))+(' · UDP' if meta.get('udp') else '')
        self.tags.setText(tags)
        state=result.get('state','untested'); value=result.get('delay')
        text={'untested':'未测试','testing':'测试中…','failed':'不可达','policy':'拦截策略'}.get(state,f'{value} ms')
        self.delay.setText(text)
        tone='good' if state=='ok' and value<200 else 'slow' if state=='ok' else 'bad' if state=='failed' else 'muted'
        self.delay.setProperty('tone',tone)
        self.setToolTip(self.name+'\n'+tags+'\n'+text+(' · '+result['time'] if result.get('time') else '')+('\n当前选中' if selected else ''))

        for widget in (self,self.delay):
            widget.style().unpolish(widget); widget.style().polish(widget); widget.update()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.title.setText(self.title.fontMetrics().elidedText(self.name,Qt.TextElideMode.ElideRight,max(10,self.width()-28)))


class NodeGrid(QWidget):
    def __init__(self):
        super().__init__(); self.box=QGridLayout(self); self.box.setContentsMargins(0,0,0,0); self.box.setSpacing(9); self.cards=[]; self.columns=0
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)

    def replace(self,cards):
        while self.box.count(): self.box.takeAt(0)
        for card in self.cards:
            if card not in cards: card.hide(); card.deleteLater()
        self.cards=cards; self.columns=0; self.reflow()

    def reflow(self):
        columns=max(1,min(5,(max(1,self.width())+9)//249))
        if columns==self.columns: return
        while self.box.count(): self.box.takeAt(0)
        for col in range(5): self.box.setColumnStretch(col,1 if col<columns else 0)
        for i,card in enumerate(self.cards): self.box.addWidget(card,i//columns,i%columns)
        self.columns=columns

    def resizeEvent(self,event):
        super().resizeEvent(event); self.reflow()


class ProxyPanel(QWidget):
    def __init__(self,owner,label,button,card):
        super().__init__(); self.owner=owner; self.label=label; self.button=button; self.controls=[]; self.groups=[]; self.metadata={}; self.delays={}; self.testing=False; self.epoch=0; self.queue=[]; self.tested=0
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(14)
        row=QHBoxLayout(); row.addWidget(button('‹ 返回插件中心',owner.close_plugin)); row.addStretch(); self.state=label('连接设备后查看状态。','subtle'); row.addWidget(self.state); layout.addLayout(row)
        frame,box=card(); box.setContentsMargins(18,15,18,15)
        row=QHBoxLayout(); row.addWidget(label('网络代理','section')); row.addStretch()
        self.mode=QComboBox()
        for title,value in [('规则模式','rule'),('全局模式','global'),('直连模式','direct')]: self.mode.addItem(title,value)
        row.addWidget(self.mode); self.controls.append(self.mode)
        for title,callback in [('应用模式',lambda:self.action('mode',{'mode':self.mode.currentData()})),('刷新',lambda:self.action('status')),('启动',lambda:self.action('start')),('停止',lambda:self.action('stop'))]:
            btn=button(title,callback); row.addWidget(btn); self.controls.append(btn)
        box.addLayout(row); layout.addWidget(frame)
        self.result=label('安装核心并导入配置后，启动服务即可显示节点。','caption',True); layout.addWidget(self.result)
        frame,box=card(); box.setContentsMargins(18,16,18,16); box.setSpacing(10)
        row=QHBoxLayout(); row.addWidget(label('代理节点','section')); self.group=QComboBox(); self.group.setMinimumWidth(150); self.group.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed); self.group.currentIndexChanged.connect(self.draw_nodes); row.addWidget(self.group,1); self.controls.append(self.group)
        self.test_all=button('测试当前组',self.start_tests,'primary'); row.addWidget(self.test_all); self.controls.append(self.test_all)
        self.cancel=button('取消',self.cancel_tests); self.cancel.hide(); row.addWidget(self.cancel); box.addLayout(row)
        row=QHBoxLayout(); self.search=QLineEdit(); self.search.setPlaceholderText('搜索节点名称 / 协议'); self.search.setClearButtonEnabled(True); self.search.textChanged.connect(self.draw_nodes); row.addWidget(self.search,1)
        self.sort=QComboBox(); self.sort.addItems(['配置顺序','延迟从低到高','名称排序']); self.sort.currentIndexChanged.connect(self.draw_nodes); self.sort.activated.connect(self.draw_nodes); row.addWidget(self.sort); self.controls.extend([self.search,self.sort]); box.addLayout(row)
        self.group_hint=label('启动代理后自动读取策略组。','caption',True); box.addWidget(self.group_hint)
        self.progress=label('','caption'); self.progress.hide(); box.addWidget(self.progress)
        self.grid=NodeGrid(); box.addWidget(self.grid); self.controls.append(self.grid)
        self.empty=label('暂无节点，请先启动代理并刷新。','subtle',True); self.empty.setMinimumHeight(70); box.addWidget(self.empty)
        row=QHBoxLayout(); row.addWidget(label('测速地址','caption')); self.test_url=QLineEdit('https://www.gstatic.com/generate_204'); row.addWidget(self.test_url,1); self.controls.append(self.test_url); box.addLayout(row)
        box.addWidget(label('延迟为泰山派经该节点访问测速地址的耗时，超时 5 秒。不可达仅表示本次测试失败；REJECT 是拦截策略。','caption',True)); layout.addWidget(frame)
        self.diagnostics=DiagnosticsPanel(self,label,button,card); layout.addWidget(self.diagnostics)
        toggle=button('配置与服务管理 ▸',self.toggle_management); layout.addWidget(toggle); self.management_toggle=toggle
        self.management=QWidget(); management=QVBoxLayout(self.management); management.setContentsMargins(0,0,0,0); layout.addWidget(self.management)
        frame,box=card(); box.addWidget(label('核心与配置','section')); box.addWidget(label('Mihomo 1.19.31 · ARM64 · HTTP/SOCKS 端口 7890','subtle'))
        row=QHBoxLayout()
        for title,action in [('安装 / 升级核心','install'),('重启服务','restart'),('卸载插件','uninstall')]:
            btn=button(title,lambda checked=False,a=action:self.action(a),'danger' if action=='uninstall' else 'secondary'); row.addWidget(btn); self.controls.append(btn)
        box.addLayout(row)
        row=QHBoxLayout(); self.autostart=QCheckBox('开机自动启动'); self.autostart.clicked.connect(lambda checked:self.action('enable' if checked else 'disable')); row.addWidget(self.autostart); self.controls.append(self.autostart)
        self.allow_lan=QCheckBox('导入时允许局域网访问'); row.addWidget(self.allow_lan); self.controls.append(self.allow_lan)
        btn=button('导入本地文件',self.import_file,'primary'); row.addWidget(btn); self.controls.append(btn); box.addLayout(row)
        row=QHBoxLayout(); self.subscription_url=QLineEdit(); self.subscription_url.setPlaceholderText('粘贴 Clash / Mihomo 订阅配置链接（HTTP / HTTPS）'); self.subscription_url.setClearButtonEnabled(True); row.addWidget(self.subscription_url,1)
        btn=button('从链接导入',self.import_subscription,'primary'); row.addWidget(btn); self.controls.extend([self.subscription_url,btn]); box.addLayout(row)
        box.addWidget(label('由电脑下载订阅配置并交给核心校验；支持 YAML / JSON 配置链接，不支持仅返回 Base64 节点列表的订阅。链接不会保存，导入成功后清空。','caption',True))
        box.addWidget(label('导入前请停止服务。支持 Clash/Mihomo YAML、JSON；默认只供泰山派本机使用，局域网访问需在客户端手动设置代理。此版本不接管 TUN 或 DNS。','caption',True)); management.addWidget(frame); self.management.hide()

    def hideEvent(self,event):
        self.cancel_tests(); super().hideEvent(event)

    def toggle_management(self):
        self.management.setVisible(not self.management.isVisible()); self.management_toggle.setText('配置与服务管理 ▾' if self.management.isVisible() else '配置与服务管理 ▸')

    def reset(self):
        self.diagnostics.reset()
        self.cancel_tests(); self.epoch+=1; self.groups=[]; self.metadata={}; self.delays={}; self.group.clear(); self.grid.replace([]); self.search.clear(); self.autostart.setChecked(False); self.allow_lan.setChecked(False); self.subscription_url.clear(); self.mode.setCurrentIndex(0)
        self.state.setText('连接设备后查看状态。'); self.result.setText('安装核心并导入配置后，启动服务即可显示节点。'); self.progress.hide(); self.draw_nodes()

    def current_group(self):
        return next((g for g in self.groups if g['name']==self.group.currentData()),{})

    def render(self,data):
        if 'state' in data:
            state=data['state']; titles={'missing':'未安装','running':'运行中','stopped':'已停止','error':'检测失败','offline':'未连接'}
            self.state.setText(titles.get(state,'待检测')+' · '+('已有配置' if data.get('configured') else '未配置'))
            self.saved_autostart=data.get('autostart',False); self.autostart.setChecked(self.saved_autostart); self.owner.plugin_center.render_proxy(data)
            if state!='running':
                self.diagnostics.invalidate()
                self.cancel_tests(); self.groups=[]; self.group.clear(); self.metadata={}; self.delays={}; self.draw_nodes()
            if state=='missing' or not data.get('configured'):
                self.management.show(); self.management_toggle.setText('配置与服务管理 ▾')
        if 'output' in data: self.result.setText(data['output'])
        if 'groups' in data:
            old=self.group.currentData(); self.groups=data['groups']; self.metadata=data.get('nodes',{})
            # Drop results for nodes removed by a config/provider refresh.
            self.delays={n:r for n,r in self.delays.items() if n in self.metadata}
            self.mode.setCurrentIndex(max(0,self.mode.findData(data.get('mode','rule'))))
            self.group.blockSignals(True); self.group.clear()
            for g in self.groups: self.group.addItem(g['name'],g['name'])
            preferred=old if old in [g['name'] for g in self.groups] else ('GLOBAL' if data.get('mode')=='global' else next((g['name'] for g in self.groups if g.get('type')=='Selector' and g['name']!='GLOBAL'),''))
            self.group.setCurrentIndex(max(0,self.group.findData(preferred))); self.group.blockSignals(False); self.draw_nodes()
            self.result.setText('点击卡片切换节点；蓝色卡片表示该策略组当前选中。')

    def node_result(self,name):
        if name in self.delays: return self.delays[name]
        meta=self.metadata.get(name,{})
        if name in ('REJECT','REJECT-DROP'): return {'state':'policy'}
        if meta.get('tested'): return {'state':'ok' if meta.get('alive') else 'failed','delay':meta.get('delay'),'time':meta.get('time','')}
        return {'state':'untested'}

    def draw_nodes(self,*args,refresh_only=False):
        group=self.current_group(); names=list(dict.fromkeys(group.get('all',[]))); query=self.search.text().strip().casefold()
        shown=[n for n in names if query in (n+' '+self.metadata.get(n,{}).get('type','')).casefold()]
        if self.sort.currentIndex()==1: shown.sort(key=lambda n:(self.node_result(n).get('state')!='ok',self.node_result(n).get('delay') or float('inf')))
        elif self.sort.currentIndex()==2: shown.sort(key=str.casefold)
        existing={c.name:c for c in self.grid.cards}
        if refresh_only and set(shown)==set(existing): shown=[c.name for c in self.grid.cards]
        cards=[]
        for name in shown:
            card=existing.get(name)
            if card is None:
                card=NodeCard(name,self.metadata.get(name,{}),name==group.get('now'),self.node_result(name),self.label,self.button,self.grid)
                card.chosen.connect(self.select_node); card.probe.connect(lambda n:self.start_tests([n]))
            else: card.update_node(self.metadata.get(name,{}),name==group.get('now'),self.node_result(name))
            cards.append(card)
        if cards!=self.grid.cards: self.grid.replace(cards)
        self.empty.setVisible(not shown); self.empty.setText('没有匹配的节点。' if names else '暂无节点，请启动代理后刷新。')
        tip='点击节点卡片即可切换。' if group.get('type')=='Selector' else '此策略组由核心自动选择，支持测试但不能手动切换。'
        if self.mode.currentData()=='direct': tip='当前为直连模式；切换为规则或全局模式后才会使用所选代理。'
        self.group_hint.setText(f"{len(shown)} / {len(names)} 个节点 · 当前：{group.get('now','—')} · {tip}")

    def action(self,action,data=None):
        owner=self.owner
        if not owner.require_device() or owner.busy or self.testing:
            if action in ('enable','disable'): self.autostart.setChecked(getattr(self,'saved_autostart',False))
            return
        if action=='install' and not owner.ask('安装网络代理','将安装官方 Mihomo ARM64 核心及服务脚本。安装后需导入配置，不会自动启动。'): return
        if action=='uninstall' and not owner.ask('卸载网络代理','将停止代理并移除开机入口，配置和核心保留在设备备份目录。'): return
        if action=='install': data={'confirm':True}
        serial=owner.serial
        def done(result):
            if owner.serial!=serial: return
            self.render(result)
            if action in ('select','mode','import','start','restart','stop','uninstall'): self.diagnostics.invalidate()
            if action=='import' and data and 'url' in data: self.subscription_url.clear()
            if action=='select':
                for g in self.groups:
                    if g['name']==data['group']: g['now']=data['node']
                self.draw_nodes(refresh_only=True)
            if action in ('start','restart','mode') or (action=='status' and result.get('state')=='running'):
                QTimer.singleShot(0,lambda:self.action('groups') if owner.serial==serial and self.isVisible() else None)
        owner.work(lambda:owner.api.dispatch('/api/proxy-'+action,{'serial':serial,**(data or {})}),done,'正在处理网络代理…')

    def import_file(self):
        if not self.owner.require_device() or self.owner.busy or self.testing: return
        name,_=QFileDialog.getOpenFileName(self,'导入 Clash/Mihomo 配置','','代理配置 (*.yaml *.yml *.json);;所有文件 (*)')
        if name: self.action('import',{'filename':name,'allow_lan':self.allow_lan.isChecked()})

    def import_subscription(self):
        if not self.owner.require_device() or self.owner.busy or self.testing: return
        url=self.subscription_url.text().strip()
        if not url: self.result.setText('请先粘贴订阅配置链接。'); return
        self.action('import',{'url':url,'allow_lan':self.allow_lan.isChecked()})

    def select_node(self,name):
        group=self.current_group()
        if group.get('type')!='Selector': self.result.setText('此组由核心自动选择节点，请切换到手动策略组。'); return
        if name not in group.get('all',[]) or name==group.get('now'): return
        self.action('select',{'group':group['name'],'node':name})

    def start_tests(self,names=None):
        if self.owner.busy or self.testing or not self.owner.require_device(): return
        if not isinstance(names,list): names=self.current_group().get('all',[])
        self.queue=[n for n in dict.fromkeys(names) if n not in ('REJECT','REJECT-DROP')]
        if not self.queue: self.result.setText('当前没有可测试的节点。'); return
        self.testing=True; self.epoch+=1; self.tested=0; self.total=len(self.queue); self.test_serial=self.owner.serial; self.test_endpoint=self.test_url.text().strip(); self.cancel.show(); self.progress.show(); self.next_batch(self.epoch)

    def next_batch(self,epoch):
        if not self.testing or epoch!=self.epoch or self.owner.serial!=self.test_serial: return
        if self.owner.busy: QTimer.singleShot(100,lambda:self.next_batch(epoch)); return
        batch=self.queue[:4]; self.queue=self.queue[4:]
        if not batch:
            self.testing=False; self.cancel.hide(); self.progress.setText(f'测试完成：{self.tested} / {self.total} · '+self.test_endpoint); return
        before={n:self.delays.get(n) for n in batch}
        for n in batch: self.delays[n]={'state':'testing'}
        self.draw_nodes(refresh_only=True); self.progress.setText(f'正在测试 {self.tested} / {self.total} · 每批最多 4 个节点')
        def completed(result):
            if epoch!=self.epoch or self.owner.serial!=self.test_serial: return
            self.delays.update(result['delays']); self.tested+=len(batch); self.draw_nodes(refresh_only=True)
            if self.testing: QTimer.singleShot(0,lambda:self.next_batch(epoch))
            else: self.progress.setText(f'已取消 · 已完成 {self.tested} / {self.total}')
        def failed(error):
            if epoch!=self.epoch: return
            for n,old in before.items():
                if old is None: self.delays.pop(n,None)
                else: self.delays[n]=old
            self.cancel_tests(); self.draw_nodes(refresh_only=True); self.progress.setText('测试中断：控制连接异常或测速地址无效，请检查提示。')
        serial=self.test_serial; endpoint=self.test_endpoint
        self.owner.work(lambda:self.owner.api.dispatch('/api/proxy-delay',{'serial':serial,'names':batch,'url':endpoint}),completed,'正在测试节点连通性…')
        self.owner.job.signals.error.connect(failed)

    def cancel_tests(self):
        if self.testing:
            self.testing=False; self.queue=[]; self.progress.setText('正在取消，当前批次结束后停止。')
        self.cancel.hide()
