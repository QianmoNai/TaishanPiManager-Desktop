use strict;
use warnings;
use Fcntl qw(:DEFAULT :flock);
use IO::Select;
use JSON::PP;
use Time::HiRes qw(time sleep);
$|=1; binmode STDIN; binmode STDOUT;
sub emit { print encode_json($_[0]),"\n"; }
sub readfile { my($p)=@_; open my $f,'<',$p or return ''; local $/; my $s=<$f>; close $f; return $s // ''; }
sub textfile { my $s=readfile($_[0]); $s=~s/\0+$//; return $s; }
sub owners {
 my($port)=@_; my @pids;
 for my $p (glob('/proc/[0-9]*')) {
  next if $p eq "/proc/$$";
  for my $fd (glob("$p/fd/*")) { if ((readlink($fd)//'') eq $port) { $p=~m{/(\d+)$}; push @pids,0+$1; last; } }
 }
 return \@pids;
}
sub inventory {
 my $base='/sys/firmware/devicetree/base'; my @uarts; my @ports;
 my $mux=join('',map {readfile($_)} glob('/sys/kernel/debug/pinctrl/*/pinmux-pins'));
 my $console=readfile('/proc/consoles'); my %symbols;
 for my $f (glob("$base/__symbols__/uart*")) { (my $name=$f)=~s{.*/}{}; $symbols{textfile($f)}=$name; }
 for my $alias (sort glob("$base/aliases/serial*")) {
  $alias=~/serial(\d+)$/ or next; my $n=0+$1; my $path=textfile($alias); my @active=unpack('N*',readfile("$base$path/pinctrl-0")); my @groups;
  for my $g (sort glob("$base/pinctrl/uart$n/*xfer")) {
   my @v=unpack('N*',readfile("$g/rockchip,pins")); my @pins;
   while(@v>=4) { my($bank,$pin,$func,$cfg)=splice(@v,0,4); push @pins,sprintf('GPIO%d_%s%d', $bank,chr(65+int($pin/8)),$pin%8); }
   my $ph=unpack('N',readfile("$g/phandle")||"\0\0\0\0"); (my $rel=$g)=~s/^\Q$base\E//; (my $name=$g)=~s{.*/}{};
   push @groups,{name=>$name,symbol=>$symbols{$rel}//'',pins=>\@pins,active=>(scalar(grep {$_==$ph} @active)?JSON::PP::true:JSON::PP::false)};
  }
  push @uarts,{index=>$n,path=>$path,symbol=>$symbols{$path}//'',status=>textfile("$base$path/status")||'okay',groups=>\@groups};
 }
 for my $port (sort (glob('/dev/ttyS[0-9]*'),glob('/dev/ttyUSB[0-9]*'),glob('/dev/ttyACM[0-9]*'))) {
  next unless -c $port; (my $name=$port)=~s{.*/}{}; my $reason='';
  $reason='system console' if $console=~/^\Q$name\E\s/m;
  $reason='Bluetooth UART' if $name eq 'ttyS1' && $mux=~/wireless-bluetooth.*uart1/;
  $reason='debug console UART' if $name eq 'ttyS2' && $mux=~/fiq-debugger.*uart2/;
  push @ports,{path=>$port,owners=>owners($port),reserved=>$reason};
 }
 return {ports=>\@ports,uarts=>\@uarts,model=>textfile("$base/model")};
}
if (($ARGV[0]//'') eq 'list') { emit(inventory()); exit; }
my($mode,$port,$baud,$bits,$parity,$stops,$flow)=@ARGV;
my($fh,$saved,$exclusive,$lock); my $cleaned=0;
sub cleanup {
 return if $cleaned++; if($fh) { ioctl($fh,0x5402,$saved) if defined $saved; ioctl($fh,0x540d,0) if $exclusive; close $fh; }
 close $lock if $lock;
 unlink $0 if $0=~m{^/tmp/tspi-serial-[a-f0-9]{24}\.pl$};
}
END { cleanup(); }
$SIG{TERM}=sub {exit}; $SIG{INT}=sub {exit}; $SIG{HUP}=sub {exit}; $SIG{PIPE}=sub {exit};
eval {
 die "Invalid port\n" unless ($mode//'') eq 'open' && ($port//'')=~m{^/dev/(?:tty(?:S|USB|ACM)\d+|pts/\d+)$} && -c $port;
 die "Invalid baud rate\n" unless ($baud//'')=~/^(?:1200|2400|4800|9600|19200|38400|57600|115200|230400|460800|921600|1000000|1500000|2000000)$/;
 die "Invalid parameters\n" unless ($bits//'')=~/^[5-8]$/ && ($parity//'')=~/^(none|even|odd)$/ && ($stops//'')=~/^[12]$/ && ($flow//'')=~/^(none|rtscts|xonxoff)$/;
 for my $p (@{inventory()->{ports}}) { if($p->{path} eq $port) { die "Reserved: $p->{reserved}\n" if $p->{reserved}; } }
 die "Port in use\n" if @{owners($port)};
 (my $lockname=$port)=~s{[^A-Za-z0-9]}{_}g; sysopen($lock,"/run/tspi-serial-$lockname.lock",O_WRONLY|O_CREAT,0600) or die "Lock unavailable\n";
 flock($lock,LOCK_EX|LOCK_NB) or die "Port locked\n";
 sysopen($fh,$port,O_RDWR|O_NOCTTY|O_NONBLOCK) or die "Cannot open serial port\n";
 $saved="\0"x256; ioctl($fh,0x5401,$saved) or die "Cannot save termios\n";
 ioctl($fh,0x540c,0) or die "Cannot reserve serial port\n"; $exclusive=1;
 my @args=('stty','-F',$port,'raw','-echo','clocal','-hupcl',$baud,"cs$bits",($stops==2?'cstopb':'-cstopb'),($parity eq 'none'?'-parenb':'parenb'),($parity eq 'odd'?'parodd':'-parodd'),($flow eq 'rtscts'?'crtscts':'-crtscts'),($flow eq 'xonxoff'?'ixon':'-ixon'),($flow eq 'xonxoff'?'ixoff':'-ixoff'),'min','0','time','0');
 system(@args)==0 or die "Serial parameters not supported\n";
 emit({event=>'ready'}); my $sel=IO::Select->new(\*STDIN,$fh); my $input=''; my $tx=''; my $last=time;
 while(time-$last<12) {
  for my $f ($sel->can_read(.03)) {
   my $n=sysread($f,my $buf,4096); next if !defined($n) && ($!{EAGAIN} || $!{EINTR}); die "Read failed\n" unless defined $n;
   if(fileno($f)==fileno(STDIN)) {
    if(!$n) { cleanup(); exit; } $last=time; $input.=$buf; die "Input overflow\n" if length($input)>131072;
    while($input=~s/^(.*?)\n//) {
     my $c=decode_json($1); if(($c->{cmd}//'') eq 'stop') { cleanup(); emit({event=>'closed'}); exit; }
     if(($c->{cmd}//'') eq 'send') {
      my $hex=$c->{hex}//''; die "Invalid data\n" unless $hex=~/^(?:[0-9a-fA-F]{2}){1,16384}$/;
      die "Send queue full\n" if length($tx)+length($hex)/2>65536; $tx.=pack('H*',$hex);
     }
    }
    if(!$n) { cleanup(); exit; }
   } elsif($n) { emit({event=>'rx',hex=>unpack('H*',$buf)}); } else { die "Serial device disconnected\n"; }
  }
  if(length $tx) { my $n=syswrite($fh,$tx); if(defined($n) && $n>0) { substr($tx,0,$n,''); emit({event=>'tx',count=>$n}); } elsif(!defined($n) && !$!{EAGAIN} && !$!{EINTR}) { die "Write failed\n"; } }
 }
};
if($@) { my $error=$@; $error=~s/ at .*//s; emit({event=>'error',message=>$error}); }
cleanup();
