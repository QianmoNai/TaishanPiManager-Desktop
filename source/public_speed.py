"""Domestic HTTP throughput tests on the board, with an offline node list."""
import hashlib
import json
import math
import re
import secrets
import shlex
from adb_core import UserError
from monitor_plugin import ASSETS

# Fixed public HTTP speed-test entries. No runtime directory request.
# Keep order and host/port values aligned with public_speed.pl.
NODES=(('上海 · 中国联通','mobile.shunicomtest.com',8080),
       ('苏州 · JSQY','speedtest.jsqiuying.com',8080),
       ('昆山 · 昆山杜克大学','speedtest.dukekunshan.edu.cn',8080),
       ('香港 · Leaseweb','speedtest1.hkg1.hk.leaseweb.net',80),
       ('新加坡 · Leaseweb','speedtest1.sin1.sg.leaseweb.net',80),
       ('东京 · Leaseweb','speedtest1.tyo1.jp.leaseweb.net',80),
       ('法兰克福 · Leaseweb','speedtest1.fra1.de.leaseweb.net',80),
       ('伦敦 · Leaseweb','speedtest1.lon1.uk.leaseweb.net',80),
       ('纽约 · Leaseweb','speedtest1.nyc1.us.leaseweb.net',80),
       ('旧金山 · Leaseweb','speedtest1.sfo1.us.leaseweb.net',80))


def parse_result(raw):
    try:
        data=json.loads(raw.decode('utf-8','replace'))
        if data.get('error'): raise UserError('节点测速失败：'+str(data['error']).strip())
        for key in ('mbps','bytes','seconds'):
            if type(data.get(key)) not in (int,float) or not math.isfinite(data[key]) or data[key]<=0: raise ValueError()
        if data['bytes']>16777216 or data['seconds']>18: raise ValueError()
        return data
    except (ValueError,AttributeError,TypeError): raise UserError('测速节点没有返回有效的完整结果。')


def connectivity_test(adb,serial,interface='',progress=None):
    return public_speed_test(adb,serial,interface,progress,probe_only=True)


def public_speed_test(adb,serial,interface,progress=None,probe_only=False):
    progress=progress or (lambda message:None)
    if interface and not re.fullmatch(r'[A-Za-z0-9_.:-]{1,32}',interface): raise UserError('请选择有效网卡。')
    q=shlex.quote
    command='ip -o -4 addr show dev '+q(interface) if interface else '''dev=$(ip -4 route show default | awk '{for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); exit}}')
[ -n "$dev" ] || { echo '没有默认网络路由，请先联网。'; exit 1; }
ip -o -4 addr show dev "$dev"
'''
    raw,_,_=adb.shell(serial,command,timeout=5)
    match=re.search(rb'inet (\d+\.\d+\.\d+\.\d+)/',raw)
    if not match: raise UserError('所选网卡没有 IPv4 地址，请先连接 Wi-Fi 或网线。')
    address=match[1].decode()
    if not interface:
        name=re.search(rb'^\d+:\s+([A-Za-z0-9_.:-]+)\s',raw)
        if not name: raise UserError('无法识别默认网卡。')
        interface=name[1].decode()
    helper=ASSETS/'public_speed.pl'
    if not helper.is_file(): raise UserError('公网测速组件缺失，请重新解压完整软件包。')
    path='/tmp/tspi-public-speed-'+secrets.token_hex(12)+'.pl'
    digest=hashlib.sha256(helper.read_bytes()).hexdigest()
    try:
        progress('正在准备公网测速…')
        adb.run(['-s',serial,'push',str(helper),path],timeout=10)
        adb.shell(serial,'test "$(sha256sum '+q(path)+' | cut -d " " -f 1)" = '+digest+' && LC_ALL=C LANG=C perl -c '+q(path),timeout=5)
        # Ten fixed nodes can each spend several seconds on DNS/connect/header
        # probing. Keep the board-side timeout below the ADB timeout, and show
        # the real board-side diagnostic if the command still fails.
        prefix='LC_ALL=C LANG=C timeout 90 perl '+q(path)+' '
        progress('正在检测国内及海外节点…')
        raw,err,code=adb.shell(serial,prefix+'probe '+q(address),timeout=95,check=False)
        if code:
            detail=(raw+err.encode('utf-8','replace')).decode('utf-8','replace').strip()
            raise UserError('设备公网节点探测失败。'+(('\n'+detail[:500]) if detail else ' 请检查设备的 Perl、DNS 和外网连接。'))
        try:
            nodes=json.loads(raw.decode())
            if not isinstance(nodes,list): raise ValueError()
            nodes=[n for n in nodes if isinstance(n,dict) and type(n.get('index')) is int and 0<=n['index']<len(NODES)
                   and type(n.get('latency_ms')) in (int,float) and math.isfinite(n['latency_ms']) and 0<=n['latency_ms']<4000]
            nodes.sort(key=lambda n:n['latency_ms'])
        except (ValueError,TypeError): raise UserError('国内节点检测结果无效。')
        if probe_only:
            reachable={n['index']:n['latency_ms'] for n in nodes}
            return {'interface':interface,'nodes':[
                {'name':name,'target':f'{host}:{port}','latency_ms':reachable.get(i),
                 'reachable':i in reachable}
                for i,(name,host,port) in enumerate(NODES)]}
        if not nodes: raise UserError('公网测速节点暂时均不可达。请检查泰山派外网连接，或稍后重试。')
        errors=[]
        for n in nodes:
            name,host,port=NODES[n['index']]
            result={'mode':'public','node':name,'target':f'{host}:{port}','interface':interface,'latency_ms':n['latency_ms']}
            try:
                for mode,title in (('download','下载'),('upload','上传')):
                    progress('正在测试'+title+' · '+name+'…')
                    raw,err,code=adb.shell(serial,prefix+mode+' '+q(address)+' '+str(n['index']),timeout=95,check=False)
                    if code and not raw.strip():
                        detail=err.strip() or '设备端测速命令返回失败。'
                        raise UserError(detail[:500])
                    result[mode]=parse_result(raw)
                    if code: raise UserError('测试未正常完成。')
            except UserError as exc:
                errors.append(name+'：'+str(exc).split('\n')[0]);continue
            return result
        raise UserError('公网测速未完成。\n'+'\n'.join(errors)+'\n请稍后重试；不会以失败或局域网结果代替公网速度。')
    finally:
        try: adb.shell(serial,'rm -f '+q(path),timeout=5)
        except UserError: pass
