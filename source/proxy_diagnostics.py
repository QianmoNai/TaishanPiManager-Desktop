"""HTTP diagnostics through the board's explicit proxy, never the PC's direct route."""
import base64
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import http.client
import ipaddress
import json
import ssl
import time
import urllib.parse
from adb_core import UserError


def checked_url(url):
    try:
        p=urllib.parse.urlsplit(url)
        if p.scheme not in ('https','http') or not p.hostname or p.username or p.password or len(url)>2048: raise ValueError()
        _=p.port
        return p
    except Exception: raise UserError('请输入有效的 HTTP/HTTPS 网站地址，不要包含账号密码。') from None


@contextmanager
def proxy_session(adb,serial):
    from proxy_plugin import owned,status,BASE
    owned(adb,serial)
    if status(adb,serial)['state']!='running': raise UserError('请先启动网络代理。')
    raw,_,_=adb.shell(serial,f'cat {BASE}/config.yaml')
    try:
        config=json.loads(raw); auth=config.get('authentication') or []
        headers={'Proxy-Authorization':'Basic '+base64.b64encode(auth[0].encode()).decode()} if auth else {}
    except Exception: raise UserError('无法读取代理配置。') from None
    raw,_,_=adb.run(['-s',serial,'forward','tcp:0','tcp:7890']); port=int(raw.strip())
    try: yield port,headers
    finally: adb.run(['-s',serial,'forward','--remove',f'tcp:{port}'],check=False)


def fetch(port,proxy_headers,url,limit=1024):
    started=time.monotonic()
    for _ in range(6):
        p=checked_url(url)
        if p.scheme=='https':
            conn=http.client.HTTPSConnection('127.0.0.1',port,timeout=8,context=ssl.create_default_context())
            conn.set_tunnel(p.hostname,p.port or 443,headers=proxy_headers)
            target=urllib.parse.urlunsplit(('', '',p.path or '/',p.query,'')); headers={}
        else:
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=8)
            target=urllib.parse.urlunsplit((p.scheme,p.netloc,p.path or '/',p.query,'')); headers=dict(proxy_headers)
        headers['User-Agent']='TaishanPiManager/2.26'
        try:
            conn.request('GET',target,headers=headers); response=conn.getresponse()
            code=response.status
            if code in (301,302,303,307,308):
                location=response.getheader('Location')
                if not location: raise UserError('网站重定向缺少目标地址。')
                url=urllib.parse.urljoin(url,location); continue
            body=response.read(limit+1)
            return code,body,max(1,round((time.monotonic()-started)*1000))
        finally: conn.close()
    raise UserError('网站重定向次数过多。')


def websites(adb,serial,data):
    urls=data.get('urls',[])
    if not isinstance(urls,list) or not 1<=len(urls)<=8: raise UserError('每次最多测试 8 个网站。')
    for url in urls: checked_url(url)
    with proxy_session(adb,serial) as (port,headers):
        def probe(url):
            try:
                code,_,delay=fetch(port,headers,url)
                return url,{'state':'ok' if 200<=code<400 else 'http_error','status':code,'delay':delay}
            except Exception: return url,{'state':'failed','message':'连接失败 / 超时'}
        with ThreadPoolExecutor(max_workers=4) as pool: result=dict(pool.map(probe,urls))
    return {'sites':result,'time':time.strftime('%H:%M:%S')}


def ip_info(adb,serial):
    try:
        with proxy_session(adb,serial) as (port,headers):
            code,raw,_=fetch(port,headers,'https://ipwho.is/',65536)
        if code!=200 or len(raw)>65536: raise ValueError()
        data=json.loads(raw)
        if data.get('success') is not True: raise ValueError()
        address=str(ipaddress.ip_address(data['ip']))
        connection=data.get('connection') or {}; timezone=data.get('timezone') or {}
        def clean(value): return str(value or '—').replace('\n',' ')[:160]
        return {'ip':address,'country':clean(data.get('country')),'country_code':clean(data.get('country_code')),
                'location':clean(', '.join(str(data.get(k) or '') for k in ('region','city')).strip(', ')),
                'isp':clean(connection.get('isp')),'org':clean(connection.get('org')),
                'asn':clean(connection.get('asn')),'timezone':clean(timezone.get('id')),
                'time':time.strftime('%H:%M:%S'),'source':'ipwho.is'}
    except UserError: raise
    except Exception: raise UserError('出口 IP 查询失败，请检查代理连通性或稍后重试。') from None
