"""Remove only this installer's scripts, retaining counters, logs and backups."""
import secrets
import shlex
from adb_core import UserError
from monitor_plugin import NAMES


def uninstall_script(backup):
    paths=['/userdata/bin/'+name for name in NAMES]+['/etc/init.d/S95check-monitor']
    q=shlex.quote
    script='''set -e
umask 077
[ "$(id -u)" = 0 ] || { echo '卸载需要 root ADB 权限。'; exit 1; }
# Refuse symlinked parent directories; never follow them while removing files.
for d in /userdata /userdata/bin /userdata/monitor-backups /etc/init.d; do
  [ ! -L "$d" ] || { echo "目录是符号链接，已停止卸载: $d"; exit 1; }
done
mkdir /var/run/tspi-monitor-control.lock || { echo '插件正忙，请稍后重试。'; exit 1; }
committed=0
backed_up=0
finish() {
  result=$?
  trap - EXIT INT TERM HUP
  if [ "$committed" = 0 ] && [ "$backed_up" = 1 ]; then
'''
    for path in paths:
        name=path.rsplit('/',1)[1]
        script+=f'    if [ -f {q(backup+"/"+name)} ]; then cp -p {q(backup+"/"+name)} {q(path)} || echo "恢复失败: {path}"; fi\n'
    script+='''    echo '卸载未完成，已尝试恢复插件文件。服务可能已停止，请查看状态。'
  fi
  rmdir /var/run/tspi-monitor-control.lock || true
  exit "$result"
}
trap finish EXIT
trap 'exit 1' INT TERM HUP
'''
    for path in paths:
        script+=f'''if [ -e {q(path)} ] || [ -L {q(path)} ]; then
  [ ! -L {q(path)} ] && [ -f {q(path)} ] && grep -q '^# TSPI_MANAGER_MONITOR_V1$' {q(path)} || {{ echo '文件非本插件管理，已停止卸载: {path}'; exit 1; }}
fi
'''
    script+='''for f in /var/run/check_lan_monitor.pid /tmp/tspi-traffic.json; do
  [ ! -L "$f" ] && { [ ! -e "$f" ] || [ -f "$f" ]; } || { echo "运行文件类型异常: $f"; exit 1; }
done
'''
    script+='mkdir -p /userdata/monitor-backups\nmkdir '+q(backup)+'\n'
    for path in paths:
        script+=f'if [ -f {q(path)} ]; then cp -p {q(path)} {q(backup+"/"+path.rsplit("/",1)[1])}; fi\n'
    script+=r'''backed_up=1
# Match complete argv entries; never kill a process solely by an untrusted PID.
owned_process() {
  [ -r "/proc/$1/cmdline" ] || return 1
  tr '\000' '\n' < "/proc/$1/cmdline" | grep -Fx -e /userdata/bin/traffic_sampler.pl -e /userdata/bin/check_monitor_daemon >/dev/null
}
for cmdline in /proc/[0-9]*/cmdline; do
  pid=${cmdline#/proc/}; pid=${pid%/cmdline}
  owned_process "$pid" || continue
  kill "$pid" 2>/dev/null || { owned_process "$pid" && exit 1; }
  tries=0
  while owned_process "$pid"; do
    tries=$((tries+1))
    [ "$tries" -le 12 ] || { echo '监控未能停止，保留插件文件。'; exit 1; }
    sleep 1
  done
done
'''
    # Removal is explicit; no data directories or unrelated files are traversed.
    for path in paths: script+='rm -f '+q(path)+'\n'
    script+='rm -f /var/run/check_lan_monitor.pid /tmp/tspi-traffic.json\n'
    for path in paths: script+='[ ! -e '+q(path)+' ] && [ ! -L '+q(path)+' ]\n'
    script+='committed=1\necho UNINSTALL_OK\n'
    return script


def uninstall(adb,serial,data):
    if data.get('confirm') is not True: raise UserError('请先确认卸载插件。')
    backup='/userdata/monitor-backups/uninstall-'+secrets.token_hex(10)
    out,_,_=adb.shell(serial,uninstall_script(backup),timeout=45)
    if 'UNINSTALL_OK' not in out.decode('utf-8','replace').splitlines():
        raise UserError('未确认卸载完成，请刷新插件状态。')
    return {'state':'missing','output':'插件已卸载，监控服务与开机启动已移除。\n累计流量和日志已保留，重新安装后可继续统计。\n插件文件备份：'+backup}
