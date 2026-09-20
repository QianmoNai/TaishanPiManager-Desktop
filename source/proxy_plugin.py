"""Mihomo installer and localhost-only controller over temporary ADB forwarding."""
import gzip
import hashlib
import json
from pathlib import Path
import secrets
import shlex
import sys
import tempfile
import urllib.request
import urllib.parse
import urllib.error
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import time
import yaml
from adb_core import UserError

VERSION = '1.19.31'
ARCHIVE_SHA256 = '9e0f11afbf38426b8bd88fdc594678f8161c57eccb4e1b77acb12b493904f1d4'
ASSETS = (Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent) / 'plugins/network-proxy'
BASE = '/userdata/network-proxy'
SERVICE = BASE + '/proxy-service'
INIT = '/etc/init.d/S96tspi-proxy'
q = shlex.quote


def owned(adb, serial):
    adb.shell(serial, f'test ! -L {BASE} && test -f {BASE}/.tspi-owned && grep -qx TSPI_MANAGER_PROXY_V1 {BASE}/.tspi-owned || {{ echo "插件未安装或目录不属于本插件。"; exit 1; }}')


def status(adb, serial):
    out, _, _ = adb.shell(serial, f'if [ -x {SERVICE} ] && [ -f {BASE}/.tspi-owned ]; then {SERVICE} status; else echo missing; fi', timeout=8)
    lines = out.decode().splitlines()
    return {'state': next((s for s in lines if s in ('missing', 'running', 'stopped')), 'error'),
            'configured': 'configured' in lines, 'autostart': 'autostart' in lines}


def install(adb, serial, data):
    if data.get('confirm') is not True: raise UserError('请先确认安装网络代理插件。')
    archive = ASSETS / 'mihomo.gz'
    if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise UserError('Mihomo 官方核心校验失败，请重新解压完整软件包。')
    token = secrets.token_hex(8)
    stage = '/userdata/.tspi-proxy-' + token
    backup = '/userdata/proxy-backups/' + token
    preflight = f'''set -e
[ "$(id -u)" = 0 ]
[ "$(uname -m)" = aarch64 ] || {{ echo '当前插件需要 ARM64 Linux。'; exit 1; }}
[ -d /userdata ] && [ -w /userdata ]
for cmd in sha256sum readlink nohup cp mv chmod sh perl; do command -v "$cmd" >/dev/null; done
perl -MJSON::PP -e 'exit 0'
if [ -e {BASE} ] || [ -L {BASE} ]; then
    [ ! -L {BASE} ] && [ -f {BASE}/.tspi-owned ] && grep -qx TSPI_MANAGER_PROXY_V1 {BASE}/.tspi-owned || {{ echo '目标目录已有其他程序，已取消覆盖。'; exit 1; }}
    if {SERVICE} status | grep -qx running; then echo '请先停止代理再升级。'; exit 1; fi
fi
if [ -e {INIT} ] || [ -L {INIT} ]; then
    [ ! -L {INIT} ] && grep -qx '# TSPI_MANAGER_PROXY_V1' {INIT} || exit 1
fi
'''
    adb.shell(serial, preflight)
    adb.shell(serial, 'umask 077; mkdir ' + stage)
    names = ('mihomo', 'proxy-service', 'S96tspi-proxy', '.tspi-owned')
    locked = False
    try:
        adb.shell(serial, 'mkdir /run/tspi-proxy.lock || { echo "代理操作正在进行。"; exit 1; }')
        locked = True
        adb.shell(serial, preflight)
        with tempfile.TemporaryDirectory(prefix='tspi-core-') as tmp:
            core = Path(tmp) / 'mihomo'
            core.write_bytes(gzip.decompress(archive.read_bytes()))
            marker = Path(tmp) / '.tspi-owned'; marker.write_text('TSPI_MANAGER_PROXY_V1\n', encoding='ascii', newline='\n')
            for name, path in (('mihomo', core), ('proxy-service', ASSETS/'proxy-service'), ('S96tspi-proxy', ASSETS/'S96tspi-proxy'), ('.tspi-owned', marker)):
                adb.run(['-s', serial, 'push', str(path), stage+'/'+name], timeout=90)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                adb.shell(serial, f'test "$(sha256sum {stage}/{name} | cut -d " " -f 1)" = {digest}')
        adb.shell(serial, f'set -e\nchmod 700 {stage}/mihomo; {stage}/mihomo -v; sh -n {stage}/proxy-service; sh -n {stage}/S96tspi-proxy')
        targets = [(n, INIT if n == 'S96tspi-proxy' else BASE+'/'+n) for n in names]
        script = f'set -e\numask 077\nmkdir -p {BASE} /userdata/proxy-backups\nchmod 700 {BASE}\nmkdir {backup}\n'
        for n, target in targets:
            script += f'[ ! -L {target} ]\nif [ -f {target} ]; then cp -p {target} {backup}/{n}; else touch {backup}/{n}.absent; fi\n'
        script += 'committed=0\nrollback() {\n[ "$committed" = 0 ] || return\n'
        for n, target in targets:
            script += f'if [ -f {backup}/{n}.absent ]; then rm -f {target}; else cp -p {backup}/{n} {target}; fi\n'
        script += '}\ntrap rollback EXIT\ntrap "exit 1" INT TERM HUP\n'
        for n, target in targets: script += f'cp {stage}/{n} {target}\nchmod {"600" if n == ".tspi-owned" else "755"} {target}\n'
        script += 'sync\ncommitted=1\necho INSTALL_OK\n'
        adb.shell(serial, script, timeout=30)
        return {**status(adb, serial), 'output': f'Mihomo {VERSION} 已安装。请导入配置后启动。\n备份：{backup}'}
    finally:
        cleanup = 'rm -f ' + ' '.join(stage+'/'+n for n in names) + '; rmdir ' + stage
        if locked: cleanup += '; rmdir /run/tspi-proxy.lock'
        try: adb.shell(serial, cleanup, check=False)
        except UserError: pass


def normalize_config(raw, allow_lan=False):
    if len(raw) > 4*1024*1024: raise UserError('配置文件不能超过 4 MB。')
    try:
        config = yaml.safe_load(raw)
        if not isinstance(config, dict): raise ValueError()
        # JSON serialization rejects recursive YAML aliases and unsupported objects.
        json.dumps(config)
    except Exception: raise UserError('无法解析配置，请选择有效的 Clash/Mihomo YAML 或 JSON 文件。') from None
    for section in ('proxy-providers', 'rule-providers'):
        providers = config.get(section, {})
        if not isinstance(providers, dict): raise UserError('provider 配置格式无效。')
        for item in providers.values():
            if not isinstance(item, dict): raise UserError('provider 配置格式无效。')
            path = item.get('path', '')
            if not isinstance(path, str) or path.startswith(('/', '\\')) or '..' in path.replace('\\', '/').split('/') or ':' in path:
                raise UserError('provider 文件路径必须位于插件目录内。')
    # Only expose the explicit proxy ports chosen by the user. Controller is local.
    for key in ('port', 'socks-port', 'redir-port', 'tproxy-port', 'listeners', 'external-controller-tls', 'external-controller-unix', 'external-controller-pipe', 'external-ui', 'external-ui-url'):
        config.pop(key, None)
    config.update({'mixed-port': 7890, 'allow-lan': bool(allow_lan), 'bind-address': '*' if allow_lan else '127.0.0.1',
                   'external-controller': '127.0.0.1:9090', 'secret': secrets.token_urlsafe(32),
                   'tun': {'enable': False}, 'dns': {'enable': False}, 'log-level': 'warning', 'profile': {'store-selected': True}})
    if config.get('mode', 'rule') not in ('rule', 'global', 'direct'): raise UserError('不支持的代理模式。')
    return config


def download_subscription(url):
    """Fetch a Clash configuration without logging or persisting the subscription URL."""
    def validate(value):
        try:
            parts=urllib.parse.urlsplit(value)
            valid=parts.scheme in ('http','https') and parts.hostname and not parts.username and not parts.password
        except (ValueError,TypeError): valid=False
        if not valid: raise UserError('请输入有效的 HTTP/HTTPS 订阅配置链接。')
    if not isinstance(url,str) or len(url)>8192: raise UserError('订阅链接无效。')
    url=url.strip(); validate(url)
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            validate(newurl)
            return super().redirect_request(req,fp,code,msg,headers,newurl)
    try:
        req=urllib.request.Request(url,headers={'User-Agent':'clash.meta','Accept':'application/yaml, application/json, text/yaml, text/plain, */*'})
        with urllib.request.build_opener(Redirect()).open(req,timeout=20) as response:
            raw=response.read(4*1024*1024+1)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise UserError('订阅下载失败，请检查链接有效期、网络连接及服务端状态。') from None
    except Exception:
        raise UserError('订阅下载失败，请检查链接有效期、网络连接及服务端状态。') from None
    if len(raw)>4*1024*1024: raise UserError('订阅配置不能超过 4 MB。')
    if not raw.strip(): raise UserError('订阅返回空内容。')
    return raw


def import_config(adb, serial, data):
    owned(adb, serial)
    if status(adb, serial)['state'] == 'running': raise UserError('请先停止代理，再导入新配置。')
    if 'url' in data:
        raw=download_subscription(data['url'])
    else:
        path = Path(data.get('filename', ''))
        if not path.is_file() or path.stat().st_size > 4*1024*1024: raise UserError('请选择不超过 4 MB 的配置文件。')
        raw=path.read_bytes()
    config = normalize_config(raw, data.get('allow_lan') is True)
    stage = BASE + '/import-' + secrets.token_hex(8)
    adb.shell(serial, f'umask 077; mkdir {stage}')
    try:
        with tempfile.TemporaryDirectory(prefix='tspi-config-') as tmp:
            file = Path(tmp)/'config.yaml'; file.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
            adb.run(['-s', serial, 'push', str(file), stage+'/config.yaml'], timeout=30)
        # Test against the real data directory so relative provider paths resolve.
        _, _, code = adb.shell(serial, f'{BASE}/mihomo -t -d {BASE} -f {stage}/config.yaml > {stage}/validation.log 2>&1', timeout=90, check=False)
        if code: raise UserError('核心拒绝该配置。请检查节点格式、规则数据库和 provider 地址；原配置未修改。')
        adb.shell(serial, f'''set -e
umask 077
[ ! -L {BASE}/config.yaml ]
if [ -f {BASE}/config.yaml ]; then cp -p {BASE}/config.yaml {BASE}/config.previous.yaml; fi
chmod 600 {stage}/config.yaml
mv {stage}/config.yaml {BASE}/config.yaml
sync
''')
        return {**status(adb, serial), 'output': '配置已验证并保存到泰山派。HTTP/SOCKS 端口：7890。点击启动服务生效。'}
    finally:
        adb.shell(serial, f'rm -f {stage}/config.yaml {stage}/validation.log; rmdir {stage}', check=False)


class DelayUnavailable(Exception):
    pass


@contextmanager
def controller_session(adb, serial):
    owned(adb, serial)
    if status(adb, serial)['state'] != 'running': raise UserError('请先启动网络代理。')
    raw, _, _ = adb.shell(serial, f'cat {BASE}/config.yaml')
    try: config = json.loads(raw)
    except Exception: raise UserError('控制配置无法读取，请重新导入。') from None
    raw, _, _ = adb.run(['-s', serial, 'forward', 'tcp:0', 'tcp:9090'])
    port = int(raw.strip())
    def request(method, path, payload=None):
        try:
            req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=None if payload is None else json.dumps(payload).encode(), method=method,
                                         headers={'Authorization': 'Bearer '+config['secret'], 'Content-Type': 'application/json'})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=9) as response:
                body = response.read(4*1024*1024)
            return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            exc.close()
            if '/delay?' in path and exc.code in (503,504): raise DelayUnavailable() from None
            raise UserError('代理控制接口请求失败，请刷新状态或检查核心。') from None
        except Exception: raise UserError('代理控制接口连接失败，请检查 ADB 和核心状态。') from None
    try:
        yield request
    finally:
        adb.run(['-s', serial, 'forward', '--remove', f'tcp:{port}'], check=False)


def controller(adb, serial, method, path, payload=None):
    with controller_session(adb,serial) as request:
        return request(method,path,payload)


def proxy_snapshot(proxies, mode):
    groups = [{'name': n, 'type': p.get('type'), 'now': p.get('now', ''), 'all': p.get('all', [])} for n,p in proxies.items() if 'all' in p]
    nodes = {}
    for name,p in proxies.items():
        history=p.get('history') or []
        last=history[-1] if history else {}
        delay=last.get('delay')
        nodes[name]={'type':p.get('type','Unknown'), 'udp':bool(p.get('udp')), 'now':p.get('now',''),
                     'delay':delay if isinstance(delay,(int,float)) and delay>0 else None,
                     'tested':bool(history), 'alive':isinstance(delay,(int,float)) and delay>0, 'time':last.get('time','')}
    return {'groups':groups,'nodes':nodes,'mode':mode}


def test_delays(adb,serial,data):
    names=data.get('names',[]); url=data.get('url','https://www.gstatic.com/generate_204')
    if not isinstance(names,list) or not 1<=len(names)<=4 or any(not isinstance(n,str) or not n for n in names):
        raise UserError('每批最多测试 4 个节点。')
    if not isinstance(url,str) or len(url)>2048: raise UserError('测速地址无效。')
    try: parts=urllib.parse.urlsplit(url)
    except ValueError: raise UserError("测速地址无效。") from None
    if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password:
        raise UserError('请输入不含账号密码的 HTTP/HTTPS 测速地址。')
    with controller_session(adb,serial) as request:
        def probe(name):
            # Reject is an intentional policy, not a broken proxy server.
            if name in ('REJECT','REJECT-DROP'): return name,{'state':'policy','delay':None}
            query=urllib.parse.urlencode({'url':url,'timeout':5000})
            path='/proxies/'+urllib.parse.quote(name,safe='')+'/delay?'+query
            try:
                response=request('GET',path)
                delay=response.get('delay')
                if not isinstance(delay,(int,float)) or delay<=0: raise DelayUnavailable()
                result={'state':'ok','delay':delay}
            except DelayUnavailable:
                result={'state':'failed','delay':None}
            # Transport/authentication errors abort the batch; do not label nodes dead.
            return name,{**result,'time':time.strftime('%H:%M:%S')}
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=dict(pool.map(probe,names))
    return {'delays':results,'url':url}


def dispatch(adb, serial, action, data):
    if action in ('websites','ip-info'):
        from proxy_diagnostics import websites,ip_info
        return websites(adb,serial,data) if action=='websites' else ip_info(adb,serial)
    if action == 'status': return status(adb, serial)
    if action == 'install': return install(adb, serial, data)
    if action == 'import': return import_config(adb, serial, data)
    if action == 'uninstall':
        owned(adb, serial)
        adb.shell(serial, SERVICE+' stop', timeout=20)
        backup='/userdata/proxy-backups/removed-'+secrets.token_hex(8)
        adb.shell(serial, f'set -e\numask 077\nmkdir -p /userdata/proxy-backups\n[ ! -L {INIT} ]\nif [ -f {INIT} ]; then grep -qx "# TSPI_MANAGER_PROXY_V1" {INIT}; rm -f {INIT}; fi\nmv {BASE} {backup}\nsync')
        return {'state':'missing','output':'已卸载。核心与配置备份在：'+backup}
    if action == 'delay': return test_delays(adb,serial,data)
    if action == 'groups':
        with controller_session(adb,serial) as request:
            proxies=request('GET','/proxies').get('proxies',{})
            mode=request('GET','/configs').get('mode','rule')
        return proxy_snapshot(proxies,mode)
    if action == 'select':
        group, node = data.get('group'), data.get('node')
        if not isinstance(group, str) or not isinstance(node, str): raise UserError('请选择策略组与节点。')
        controller(adb, serial, 'PUT', '/proxies/'+urllib.parse.quote(group, safe=''), {'name': node})
        return {'output': '节点已切换。'}
    if action == 'mode':
        mode = data.get('mode')
        if mode not in ('rule', 'global', 'direct'): raise UserError('代理模式无效。')
        controller(adb, serial, 'PATCH', '/configs', {'mode': mode})
        # The core does not persist mode changes itself; keep the boot config in sync.
        adb.shell(serial, f'''umask 077
perl -MJSON::PP -0777 -e '$p="{BASE}/config.yaml"; open F,"<",$p or die; $c=decode_json(<F>); close F; $c->{{mode}}="{mode}"; open F,">","$p.tmp" or die; print F encode_json($c); close F or die; rename "$p.tmp",$p or die;'
sync''')
        return {'output': '代理模式已切换并保存。'}
    if action in ('start', 'stop', 'restart', 'enable', 'disable'):
        owned(adb, serial)
        adb.shell(serial, SERVICE+' '+action, timeout=110)
        return {**status(adb, serial), 'output': '操作完成。'}
    raise UserError('不支持的代理操作。')
