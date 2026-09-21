# LD06 雷达视图 v1.0

可选板端插件。管理器中点击安装后将经过 SHA256 与 Perl 语法校验的采集助手安装到 /userdata/bin/tspi-ld06-helper.pl。无需编译 SDK。

UART3 /dev/ttyS3，230400 波特率，8N1。LD06 5V 接排针4、GND接6、TX接10、PWM接GND。插件仅接收，不发送雷达控制指令。

使用下载包手动安装：将 ld06-helper.pl 传到板端临时目录，执行 perl -c 校验后放到 /userdata/bin/tspi-ld06-helper.pl 并 chmod 700。建议直接在管理器使用安装按钮完成校验。

开始采集前关闭其他串口程序或暂停原 LD06/LCD 监控，结束后恢复。关闭采集恢复串口参数，ADB失联后心跳超时释放串口。

支持 CRC8 验证、显示半径1/2/3/6/12米、信号强度过滤、导出当前点云CSV。显示量程不是精度保证。
