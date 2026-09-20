import os,sys
os.environ['QT_QPA_PLATFORM']='offscreen'
sys.path.insert(0,'source')
from test_desktop import FakeApi
from desktop import Window,configure_app,apply_theme
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
app=QApplication([]); configure_app(app); w=Window(FakeApi(),autostart=False); w.timer.stop(); w.show(); w.go(4,False); w.open_proxy()
names=['香港 01 · 高速线路','香港 02 · 专线','日本 01 · 东京','新加坡 01','美国 01 · 洛杉矶','台湾 01','德国 01','英国 01','超长名称演示 · 香港高级节点移动联通电信线路','DIRECT','REJECT']
w.proxy.render({'groups':[{'name':'节点选择','type':'Selector','now':names[0],'all':names},{'name':'自动选择','type':'URLTest','now':names[0],'all':names[:4]}],'nodes':{n:{'type':'Direct' if n=='DIRECT' else 'Shadowsocks','udp':True} for n in names},'mode':'rule'})
w.proxy.delays={n:{'state':'ok','delay':38+i*47} for i,n in enumerate(names[:6])}; w.proxy.delays[names[6]]={'state':'failed'}; w.proxy.draw_nodes(); w.proxy.state.setText('运行中 · 已有配置')
for theme,width in [('light',1200),('dark',1200),('dark',1000)]:
    apply_theme(app,theme); w.resize(width,820 if width==1200 else 740); QTest.qWait(200); w.grab().save(f'v2.24-proxy-{theme}-{width}.png')
    print(theme,width,'actual',w.width(),'columns',w.proxy.grid.columns,'card widths',[c.width() for c in w.proxy.grid.cards[:3]])
w.close()
