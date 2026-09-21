"""Board traffic snapshots and bounded iperf3 tests, independent of the UI worker."""
import json
import re
import secrets
import shlex
import socket
import subprocess
import time
from pathlib import Path
from adb_core import PORTABLE, UserError


def status(adb, serial):
    raw, _, _ = adb.shell(serial, '''
if ! test -f /userdata/bin/traffic_sampler.pl; then echo '{"state":"missing"}'; exit 0; fi
if ! /userdata/bin/status_check_monitor | grep -q '^RUNNING '; then echo '{"state":"stopped"}'; exit 0; fi
cat /tmp/tspi-traffic.json || exit 1
printf '\n'; cat /proc/uptime
''', timeout=5)
    lines=raw.decode().replace('\r','').strip().splitlines()
    data=json.loads(lines[0])
    if 'state' in data: return data
    if data.get('version') != 2: raise UserError('监控插件版本不匹配，请安装新版插件。')
    age=float(lines[1].split()[0])-data['uptime']
    if not 0<=age<10: return {'state':'stale'}
    data['state']='running'
    return data


def parse_iperf(raw):
    try: data=json.loads(raw.decode('utf-8','replace'))
    except ValueError: raise UserError('测速工具没有返回有效结果。')
    if data.get('error'): raise UserError('测速失败：'+data['error'])
    end=data.get('end',{})
    result=end.get('sum_received',{})
    if 'bits_per_second' not in result or result.get('seconds',0)<=0:
        raise UserError('测速结果不完整，请检查目标服务器。')
    return {'mbps':max(0,float(result['bits_per_second']))/1e6,'bytes':result.get('bytes',0)}


def speed_test(adb, serial, interface, host='', port=5201):
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,32}',interface): raise UserError('请选择有效网卡。')
    if host and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}',host): raise UserError('测速服务器请填写 IPv4 地址或域名。')
    if not isinstance(port,int) or not 1<=port<=65535: raise UserError('端口无效。')
    q=shlex.quote
    raw,_,_=adb.shell(serial,'command -v iperf3 >/dev/null && command -v timeout >/dev/null && ip -o -4 addr show dev '+q(interface),timeout=5)
    match=re.search(rb'inet (\d+\.\d+\.\d+\.\d+)/',raw)
    if not match: raise UserError('该网卡没有 IPv4 地址，或设备缺少 iperf3 / timeout。')
    board_ip=match[1].decode(); local=not host; token=secrets.token_hex(12)
    pidfile='/tmp/tspi-speed-'+token+'.pid'; logfile='/tmp/tspi-speed-'+token+'.log'
    def cleanup():
        script=f'''if [ -f {pidfile} ]; then
p=$(cat {pidfile}); case "$p" in ''|*[!0-9]*) p=0;; esac
if [ "$p" -gt 1 ] && [ -r /proc/$p/cmdline ]; then
cmd=$(tr '\\000' ' ' < /proc/$p/cmdline)
case "$cmd" in *'{pidfile}'*) kill "$p" 2>/dev/null || true;; esac
fi
fi
rm -f {pidfile} {logfile}
'''
        try: adb.shell(serial,script,timeout=5)
        except UserError: pass  # Board-side timeout bounds lifetime even if USB is lost.
    try:
        if local:
            executable=PORTABLE/'iperf3'/'iperf3.exe'
            if not executable.is_file(): raise UserError('随包 iperf3 缺失，请解压完整软件包。')
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as route:
                route.connect((board_ip,9)); pc_ip=route.getsockname()[0]
            host=board_ip; port=40000+secrets.randbelow(20000)
        results={'target':f'{host}:{port}','interface':interface,'mode':'lan' if local else 'server'}
        for name,reverse in (('upload',False),('download',True)):
            if local:
                adb.shell(serial,f'nohup timeout 20 iperf3 -s -1 -B {q(board_ip)} -p {port} -I {pidfile} >{logfile} 2>&1 </dev/null &\nsleep 1\ntest -s {pidfile} || {{ cat {logfile}; exit 1; }}',timeout=5)
                # PC sends = board downloads; PC reverse = board uploads.
                args=[str(executable),'-J','-t','5','--connect-timeout','3000','-B',pc_ip,'-c',host,'-p',str(port)]
                if name=='upload': args.append('-R')
                try:
                    process=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                        timeout=14,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                except subprocess.TimeoutExpired: raise UserError('测速超时，请检查所选网卡与电脑的网络连通性。')
                raw=process.stdout; code=process.returncode
                cleanup()
            else:
                script='timeout 12 iperf3 -J -t 5 --connect-timeout 3000 -B '+q(board_ip)+' -c '+q(host)+' -p '+str(port)+(' -R' if reverse else '')
                raw,err,code=adb.shell(serial,script,timeout=16,check=False)
            try: results[name]=parse_iperf(raw)
            except UserError as exc:
                raise UserError(str(exc)+'\n目标 '+results['target']+'；请检查网络连通性、AP 隔离或目标测速服务器。')
            if code: raise UserError('测速未正常完成，请检查目标服务器。')
        return results
    finally:
        if local: cleanup()
