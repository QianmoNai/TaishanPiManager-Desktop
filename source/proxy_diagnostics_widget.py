"""Stable website/IP diagnostic cards for the proxy page."""
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QBoxLayout,QGridLayout,QInputDialog,QCheckBox
from proxy_diagnostics import checked_url
from adb_core import UserError

class DiagnosticsPanel(QWidget):
    def __init__(self,panel,label,button,card):
        super().__init__(); self.panel=panel; self.owner=panel.owner; self.generation=0; self.address=''; self.reveal=False; self.sites={}
        self.layout=QBoxLayout(QBoxLayout.Direction.TopToBottom,self); self.layout.setContentsMargins(0,0,0,0); self.layout.setSpacing(14)
        frame,box=card(); self.layout.addWidget(frame,1)
        row=QHBoxLayout(); row.addWidget(label('网站测试','section')); row.addStretch(); row.addWidget(button('添加',self.add_site)); row.addWidget(button('测试全部',self.test_all)); box.addLayout(row)
        self.site_grid=QGridLayout(); self.site_grid.setSpacing(8); self.site_grid.setColumnStretch(0,1); self.site_grid.setColumnStretch(1,1); box.addLayout(self.site_grid)
        self.label=label; self.button=button; self.card=card
        for name,url in [('Apple','https://www.apple.com/'),('GitHub','https://github.com/'),('Google','https://www.google.com/generate_204'),('YouTube','https://www.youtube.com/')]: self.add_tile(name,url)
        self.site_hint=label('经泰山派当前代理规则测试 · 尚未测试','caption',True); box.addWidget(self.site_hint); box.addStretch()
        frame,box=card(); self.layout.addWidget(frame,1)
        row=QHBoxLayout(); row.addWidget(label('IP 信息','section')); row.addStretch(); self.show_ip=button('显示 IP',self.toggle_ip); row.addWidget(self.show_ip); row.addWidget(button('刷新',self.refresh_ip)); box.addLayout(row)
        self.ip_text=label('IP：尚未查询','section',True); self.ip_text.setTextFormat(Qt.TextFormat.PlainText); box.addWidget(self.ip_text)
        grid=QGridLayout(); self.fields={}
        for i,(key,title) in enumerate([('country','国家/地区'),('location','位置'),('isp','服务商'),('org','组织'),('asn','ASN'),('timezone','时区')]):
            value=label(title+'：—','subtle',True); value.setMinimumWidth(0); value.setTextFormat(Qt.TextFormat.PlainText); grid.addWidget(value,i,0); self.fields[key]=(title,value)
        box.addLayout(grid)
        self.ip_hint=label('查询源：ipwho.is · 尚未查询','caption',True); box.addWidget(self.ip_hint)
        self.auto=QCheckBox('每 300 秒刷新 IP（页面可见时）'); box.addWidget(self.auto)
        box.addWidget(label('显示 IP 查询请求的出口；规则分流时，各网站出口可能不同。','caption',True)); box.addStretch()
        self.timer=QTimer(self); self.timer.setInterval(300000); self.timer.timeout.connect(self.auto_refresh); self.timer.start()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        direction=QBoxLayout.Direction.LeftToRight if self.width()>=880 else QBoxLayout.Direction.TopToBottom
        if self.layout.direction()!=direction: self.layout.setDirection(direction)

    def add_tile(self,name,url):
        frame,box=self.card(); box.setContentsMargins(10,10,10,10); box.setSpacing(5)
        title=self.label(name,'subtle'); title.setTextFormat(Qt.TextFormat.PlainText); box.addWidget(title)
        result=self.label('未测试','caption'); result.setMinimumWidth(0); box.addWidget(result)
        btn=self.button('测试',lambda:self.test_sites([url])); box.addWidget(btn)
        i=len(self.sites); self.site_grid.addWidget(frame,i//2,i%2); self.sites[url]=result; frame.setToolTip(url)

    def add_site(self):
        if len(self.sites)>=8: self.site_hint.setText('最多添加 8 个网站。'); return
        url,ok=QInputDialog.getText(self,'添加网站','HTTP / HTTPS 网站地址：')
        if not ok: return
        url=url.strip()
        try: p=checked_url(url)
        except UserError as exc: self.site_hint.setText(str(exc)); return
        if url in self.sites: return
        self.add_tile(p.hostname[:36],url)

    def available(self):
        return not self.owner.busy and not self.panel.testing and self.owner.require_device()

    def run(self,action,data,done,failed):
        if not self.available(): return False
        serial=self.owner.serial; generation=self.generation
        def complete(result):
            if serial==self.owner.serial and generation==self.generation: done(result)
        def error(_):
            if serial==self.owner.serial and generation==self.generation: failed()
        self.owner.work(lambda:self.owner.api.dispatch('/api/proxy-'+action,{'serial':serial,**data}),complete,'正在检查代理网络…')
        self.owner.job.signals.error.connect(error)
        return True

    def test_all(self): self.test_sites(list(self.sites))

    def test_sites(self,urls):
        if not self.available(): return
        def done(result):
            for url,r in result['sites'].items():
                if url not in self.sites: continue
                text=f"{r['delay']} ms · HTTP {r['status']}" if r['state']!='failed' else '连接失败 / 超时'
                if r['state']=='http_error': text='受限 · '+text
                self.sites[url].setText(text)
            self.site_hint.setText('更新于 '+result['time']+' · HTTP 错误表示站点拒绝/限制，不等于代理离线')
        def failed():
            for url in urls: self.sites[url].setText('测试未完成')
        if self.run('websites',{'urls':urls},done,failed):
            for url in urls: self.sites[url].setText('测试中…')

    def refresh_ip(self):
        def done(result):
            self.address=result['ip']; self.render_address()
            for key,(title,widget) in self.fields.items(): widget.setText(title+'：'+str(result.get(key,'—')))
            self.ip_hint.setText('查询源：ipwho.is · 更新于 '+result['time'])
        def failed():
            self.address=''; self.render_address()
            for title,widget in self.fields.values(): widget.setText(title+'：—')
            self.ip_hint.setText('查询失败，请检查网络后重试。')
        if self.run('ip-info',{},done,failed): self.ip_hint.setText('正在查询出口 IP…')

    def auto_refresh(self):
        if self.auto.isChecked() and self.isVisible() and self.owner.serial and not self.owner.busy and not self.panel.testing: self.refresh_ip()

    def render_address(self): self.ip_text.setText('IP：'+(self.address if self.reveal else '••••••••') if self.address else 'IP：尚未查询')

    def toggle_ip(self):
        self.reveal=not self.reveal; self.show_ip.setText('隐藏 IP' if self.reveal else '显示 IP'); self.render_address()

    def invalidate(self):
        self.generation+=1; self.address=''; self.render_address()
        for result in self.sites.values(): result.setText('未测试')
        for title,widget in self.fields.values(): widget.setText(title+'：—')
        self.site_hint.setText('代理配置/选择已变更，请重新测试。'); self.ip_hint.setText('请刷新当前出口信息。')

    def reset(self):
        self.invalidate(); self.reveal=False; self.show_ip.setText('显示 IP'); self.auto.setChecked(False)
