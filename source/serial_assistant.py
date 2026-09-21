"""Board serial inventory/session preparation and bounded VOFA-compatible decoding."""
from pathlib import Path
import hashlib,json,math,re,secrets,shlex,struct,sys
from adb_core import UserError
ASSETS=(Path(sys._MEIPASS) if getattr(sys,'frozen',False) else Path(__file__).resolve().parent.parent)/'plugins/serial-assistant'
BAUDS=[1200,2400,4800,9600,19200,38400,57600,115200,230400,460800,921600,1000000,1500000,2000000]

def inventory(adb,serial):
    source=(ASSETS/'serial-helper.pl').read_text(encoding='utf-8-sig')
    out,_,_=adb.shell(serial,"LC_ALL=C LANG=C perl - list <<'TSPI_SERIAL_INVENTORY'\n"+source+"\nTSPI_SERIAL_INVENTORY\n",timeout=15)
    try: return json.loads(out)
    except Exception: raise UserError('无法读取串口信息，请检查板端 Perl 和设备树。') from None

def prepare(adb,serial,port,baud,bits,parity,stops,flow):
    if not re.fullmatch(r'/dev/tty(?:S|USB|ACM)\d+',port): raise UserError('无效串口。')
    if baud not in BAUDS or bits not in (5,6,7,8) or parity not in ('none','odd','even') or stops not in (1,2) or flow not in ('none','rtscts','xonxoff'): raise UserError('无效串口参数。')
    devices=inventory(adb,serial); current=next((p for p in devices['ports'] if p['path']==port),None)
    if not current: raise UserError('串口已移除，请刷新。')
    if current['reserved'] or current['owners']: raise UserError('串口由系统保留或正在被其他进程使用，请先释放。')
    source=ASSETS/'serial-helper.pl'; target='/tmp/tspi-serial-'+secrets.token_hex(12)+'.pl'
    try:
        adb.run(['-s',serial,'push',str(source),target])
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        adb.shell(serial,f'test "$(sha256sum {target} | cut -d " " -f 1)" = {digest} && LC_ALL=C LANG=C perl -c {target}')
    except Exception:
        adb.shell(serial,'rm -f '+target,check=False); raise
    command='LC_ALL=C LANG=C perl '+target+' open '+' '.join(shlex.quote(str(v)) for v in (port,baud,bits,parity,stops,flow))
    return command

def pin_config(uart,group,enabled=True):
    index=uart.get('index'); symbol=uart.get('symbol',''); g=group.get('symbol','')
    if type(index) is not int or symbol!=f'uart{index}' or not re.fullmatch(r'uart'+str(index)+r'(?:m\d+)?_xfer',g): raise UserError('缺少可验证的设备树符号，无法导出。')
    if index in (1,2): raise UserError('UART1/2 关联蓝牙或系统控制台，此工具不生成其覆盖配置。')
    return ('/* TaishanPi UART configuration fragment. Review pin conflicts in your SDK. */\n'
            '/* Group pins: '+', '.join(group.get('pins',[]))+' */\n'
            f'&{symbol} {{\n    status = "'+('okay' if enabled else 'disabled')+'";\n'
            '    pinctrl-names = "default";\n'+f'    pinctrl-0 = <&{g}>;\n'+'};\n')

def encode_send(text,hex_mode=False,newline='none'):
    if hex_mode:
        compact=''.join(text.split())
        if not compact or len(compact)%2 or not re.fullmatch('[0-9a-fA-F]+',compact): raise UserError('HEX 数据应为完整字节，例如 01 02 FF。')
        raw=bytes.fromhex(compact)
    else: raw=text.encode('utf-8')
    raw+={'none':b'','lf':b'\n','crlf':b'\r\n','cr':b'\r'}[newline]
    if not 1<=len(raw)<=16384: raise UserError('每次发送 1～16384 字节。')
    return raw

class WaveDecoder:
    def __init__(self,mode='none'): self.mode=mode; self.buffer=bytearray(); self.dropped=0
    def feed(self,data):
        if self.mode=='none': return []
        self.buffer.extend(data); frames=[]; marker=b'\x00\x00\x80\x7f' if self.mode=='justfloat' else b'\n'
        while True:
            pos=self.buffer.find(marker)
            if pos<0: break
            raw=bytes(self.buffer[:pos]); del self.buffer[:pos+len(marker)]
            try:
                if self.mode=='justfloat':
                    if not raw or len(raw)%4 or len(raw)>32: raise ValueError()
                    values=struct.unpack('<'+'f'*(len(raw)//4),raw)
                else:
                    text=raw.decode('ascii').strip().rsplit(':',1)[-1]; values=[float(v.strip()) for v in text.split(',')]
                if not 1<=len(values)<=8 or not all(math.isfinite(v) for v in values): raise ValueError()
                frames.append(list(values))
            except (ValueError,UnicodeError,struct.error): self.dropped+=1
        if len(self.buffer)>4096: self.buffer.clear(); self.dropped+=1
        return frames
