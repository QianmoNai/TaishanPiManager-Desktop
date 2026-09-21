"""Offline installer with syntax/hash checks, owned-file protection and rollback."""
import hashlib
from pathlib import Path
import secrets
import shlex
import sys
from adb_core import UserError

NAMES = ('traffic_sampler.pl','check_orangepi.pl','check_orangepi','check_monitor_daemon','monitor_service',
         'start_check_monitor','stop_check_monitor','status_check_monitor')
ASSETS = (Path(sys._MEIPASS) if getattr(sys,'frozen',False) else Path(__file__).resolve().parent.parent)/'plugins/network-monitor'


def status(adb, serial):
    """Inspect only; a differing bundled script is an update, not uninstalled."""
    digest=hashlib.sha256((ASSETS/'traffic_sampler.pl').read_bytes()).hexdigest()
    script='''
if [ ! -f /userdata/bin/traffic_sampler.pl ]; then
  if [ -x /userdata/bin/status_check_monitor ]; then echo update; else echo missing; fi
elif [ "$(sha256sum /userdata/bin/traffic_sampler.pl | cut -d ' ' -f 1)" != '''+shlex.quote(digest)+''' ]; then
  echo update
elif /userdata/bin/status_check_monitor | grep -q '^RUNNING '; then echo running
else echo stopped
fi
'''
    out,_,_=adb.shell(serial,script,timeout=8)
    state=out.decode().strip()
    if state not in ('update','missing','running','stopped'): raise UserError('无法识别插件状态，请检查板端服务。')
    return {'state':state}


def install(adb, serial, data):
    if data.get('confirm') is not True: raise UserError('请先确认安装监控插件。')
    autostart = data.get('autostart') is True
    names = (*NAMES, 'S95check-monitor') if autostart else NAMES
    files = [(name, ASSETS/name, '/etc/init.d/'+name if name=='S95check-monitor' else '/userdata/bin/'+name) for name in names]
    if any(not file.is_file() for _,file,_ in files): raise UserError('插件资源不完整，请重新解压完整的软件包。')
    token=secrets.token_hex(10)
    stage='/tmp/tspi-monitor-install-'+token
    backup='/userdata/monitor-backups/'+token
    q=shlex.quote
    preflight = '''
set -e
[ "$(id -u)" = 0 ] || { echo '安装需要 root ADB 权限。'; exit 1; }
for command in perl sha256sum sh cp mv chmod mkdir grep tr nohup stat; do
  command -v "$command" >/dev/null || { echo "缺少依赖: $command"; exit 1; }
done
LC_ALL=C LANG=C perl -MJSON::PP -MTime::HiRes -MFcntl -e 'exit 0'
test -d /userdata && test -w /userdata || { echo '/userdata 不可写。'; exit 1; }
if [ -x /userdata/bin/status_check_monitor ]; then
  grep -q '^# TSPI_MANAGER_MONITOR_V1$' /userdata/bin/status_check_monitor || { echo '检测到旧版或其他来源的监控脚本，为避免覆盖，请先手动迁移。'; exit 1; }
  /userdata/bin/status_check_monitor | grep -q '^RUNNING ' && { echo '请先停止监控服务，再安装或升级。'; exit 1; }
fi
'''
    for _,_,target in files:
        preflight+=f'if [ -e {q(target)} ] || [ -L {q(target)} ]; then\n[ ! -L {q(target)} ] && [ -f {q(target)} ] && grep -q "^# TSPI_MANAGER_MONITOR_V1$" {q(target)} || {{ echo "目标文件非本插件所有，已停止安装: {target}"; exit 1; }}\nfi\n'
    adb.shell(serial, preflight+'\ntrue\n', timeout=15)
    adb.shell(serial, 'umask 077; mkdir '+q(stage))
    locked=False
    try:
        adb.shell(serial, 'mkdir /var/run/tspi-monitor-control.lock || { echo "监控服务忙或有未完成操作，请稍后重试。"; exit 1; }')
        locked=True
        adb.shell(serial, preflight+'\ntrue\n', timeout=15)
        for name,file,_ in files: adb.run(['-s',serial,'push',str(file),stage+'/'+name],timeout=30)
        validate='set -e\n'
        for name,file,_ in files:
            path=q(stage+'/'+name); digest=hashlib.sha256(file.read_bytes()).hexdigest()
            validate+=f'test "$(sha256sum {path} | cut -d " " -f 1)" = {digest}\n'
            validate+=(f'LC_ALL=C LANG=C perl -c {path}\n' if name.endswith('.pl') else f'sh -n {path}\n')
        adb.shell(serial,validate,timeout=20)
        # Backup every original before mutating any destination. The backup remains
        # on the board so an interrupted update can be recovered manually as well.
        transaction='set -e\numask 077\nmkdir -p /userdata/bin /userdata/monitor-backups\nmkdir '+q(backup)+'\n'
        for name,_,target in files:
            transaction+=f'if [ -f {q(target)} ]; then cp -p {q(target)} {q(backup+"/"+name)}; else touch {q(backup+"/"+name+".absent")}; fi\n'
        transaction+='committed=0\nrollback() {\n[ "$committed" = 0 ] || return\n'
        for name,_,target in files:
            transaction+=f'if [ -f {q(backup+"/"+name+".absent")} ]; then rm -f {q(target)}; else cp -p {q(backup+"/"+name)} {q(target)}; fi\n'
        transaction+='}\ntrap rollback EXIT\ntrap "exit 1" INT TERM HUP\n'
        for name,_,target in files:
            transaction+=f'cp {q(stage+"/"+name)} {q(target)}\nchmod 755 {q(target)}\n'
        transaction+='LC_ALL=C LANG=C perl -c /userdata/bin/check_orangepi.pl\n/userdata/bin/status_check_monitor\ncommitted=1\necho INSTALL_OK\n'
        out,_,_=adb.shell(serial,transaction,timeout=25)
        if 'INSTALL_OK' not in out.decode('utf-8','replace'): raise UserError('安装结果未确认，请重新查看插件状态。')
        return {'output':'监控插件安装成功。\n'+('已配置开机自动启动。\n' if autostart else '未更改已有开机启动设置。\n')+
                '点击“启动服务”开始监控。\n备份目录：'+backup+'\n'+out.decode('utf-8','replace').replace('INSTALL_OK','')}
    finally:
        # Only remove explicitly generated staging files, never recurse over device paths.
        cleanup='rm -f '+' '.join(q(stage+'/'+name) for name in names)+'; rmdir '+q(stage)
        if locked: cleanup+='; rmdir /var/run/tspi-monitor-control.lock'
        try: adb.shell(serial,cleanup,check=False)
        except UserError: pass
