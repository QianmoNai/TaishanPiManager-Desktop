#!/usr/bin/perl
# One-shot public HTTP speed test. No daemon, credentials or telemetry.
use strict;
use warnings;
use IO::Socket::INET;
use Socket qw(inet_aton inet_ntoa);
use Time::HiRes qw(time);
use JSON::PP qw(encode_json);
my @nodes=(
 ['Shanghai','mobile.shunicomtest.com',8080],
 ['Suzhou','speedtest.jsqiuying.com',8080],
 ['Kunshan','speedtest.dukekunshan.edu.cn',8080],
 ['Hong Kong','speedtest1.hkg1.hk.leaseweb.net',80],
 ['Singapore','speedtest1.sin1.sg.leaseweb.net',80],
 ['Tokyo','speedtest1.tyo1.jp.leaseweb.net',80],
 ['Frankfurt','speedtest1.fra1.de.leaseweb.net',80],
 ['London','speedtest1.lon1.uk.leaseweb.net',80],
 ['New York','speedtest1.nyc1.us.leaseweb.net',80],
 ['San Francisco','speedtest1.sfo1.us.leaseweb.net',80],
);
my ($mode,$ip,$index)=@ARGV;
die "Invalid source IPv4\n" unless defined($ip) && $ip =~ /^\d+\.\d+\.\d+\.\d+$/;
$SIG{PIPE}='IGNORE';
sub connect_node {
 my ($n)=@_;
 my $packed=inet_aton($nodes[$n][1]) or die "DNS failed\n";
 my $s=IO::Socket::INET->new(PeerAddr=>inet_ntoa($packed),PeerPort=>$nodes[$n][2],
     LocalAddr=>$ip,Proto=>'tcp',Timeout=>3) or die "Connection failed\n";
 return $s;
}
sub send_all {
 my ($s,$text)=@_; my $offset=0;
 while ($offset<length($text)) {
  my $n=syswrite($s,$text,length($text)-$offset,$offset);
  die "Send failed\n" unless defined($n) && $n>0; $offset+=$n;
 }
}
sub headers {
 my ($s)=@_; my $raw='';
 while ($raw !~ /\r\n\r\n$/) {
  my $n=sysread($s,my $c,1); die "Incomplete HTTP headers\n" unless $n;
  $raw.=$c; die "Oversized headers\n" if length($raw)>16384;
 }
 die "HTTP response rejected\n" unless $raw =~ m{^HTTP/1\.[01] 200(?: |\r)};
 die "Unsupported encoding\n" if $raw =~ /\r\n(?:Transfer-Encoding:|Content-Encoding:\s*(?!identity))/i;
 my ($len)=$raw =~ /\r\nContent-Length:\s*(\d+)/i;
 die "Missing content length\n" unless defined $len;
 return (0+$len,$raw);
}
sub request {
 my ($s,$n,$method,$file,$body_length)=@_;
 my $nonce=int(time*1000000);
  my $h="$method /speedtest/$file?x=$nonce HTTP/1.1\r\nHost: $nodes[$n][1]:$nodes[$n][2]\r\nUser-Agent: TaishanPiManager/2.10\r\nAccept-Encoding: identity\r\nCache-Control: no-cache\r\nConnection: close\r\n";
 $h.="Content-Type: application/x-www-form-urlencoded\r\nContent-Length: $body_length\r\n" if $method eq 'POST';
 send_all($s,$h."\r\n");
}
sub read_body {
 my ($s,$len)=@_; die "Oversized response\n" if $len>4096;
 my $body='';
 while(length($body)<$len) {my $n=sysread($s,my $buf,$len-length($body));die "Truncated response\n" unless $n;$body.=$buf;}
 return $body;
}
if ($mode eq 'probe') {
 my @found;
 for my $n (0..$#nodes) {
  my @times;
  for (1..2) {
   eval {
    local $SIG{ALRM}=sub {die "timeout\n"};alarm 4;
    my $t=time;my $s=connect_node($n);request($s,$n,'GET','latency.txt',0);
    my ($len)=headers($s);my $body=read_body($s,$len);
    die "Invalid probe response\n" unless $body =~ /^test=test\s*$/;
    push @times,(time-$t)*1000;close $s;alarm 0;
   };alarm 0;
  }
  push @found,{index=>$n,latency_ms=>(sort {$a<=>$b} @times)[0]} if @times;
 }
 print encode_json(\@found);exit 0;
}
die "Invalid node\n" unless defined($index) && $index =~ /^\d$/ && $index<@nodes;
my ($count,$elapsed,$error,$window_done)=(0,0,'',0);
eval {
 local $SIG{ALRM}=sub {die "Network timeout\n"};alarm 15;
 my $s=connect_node($index);my $start=time;
 if ($mode eq 'download') {
  request($s,$index,'GET','random4000x4000.jpg',0);
  my ($length,$h)=headers($s);
  die "Unexpected download content\n" unless $h =~ /Content-Type:\s*image\/jpeg/i && $length>=262144;
  my $limit=$length<16777216 ? $length : 16777216;
  # A fixed 8s sample window, at most 16 MiB. Only our deliberate
  # sample deadline permits partial content; transport truncation is failure.
  eval {
   local $SIG{ALRM}=sub {$window_done=1;die "sample deadline\n"};alarm 8;
   while ($count<$limit) {
    my $size=$limit-$count; $size=65536 if $size>65536;
    my $n=sysread($s,my $buf,$size);die "Download truncated\n" unless $n;
    $count+=$n;
   }
  };
  die $@ if $@ && !$window_done;
  die "Too little download data\n" if $count<65536;
 } elsif ($mode eq 'upload') {
  # Ramp payloads from 128 KiB so slower domestic routes can still finish.
  for my $length (131072,524288,2097152) {
   request($s,$index,'POST','upload.php',$length);
   send_all($s,'content1=');my $sent=9;
   while ($sent<$length) {my $size=$length-$sent;$size=65536 if $size>65536;send_all($s,'0'x$size);$sent+=$size;}
   my ($len)=headers($s);my $body=read_body($s,$len);
   die "Upload not acknowledged\n" unless $body =~ /\bsize=$length\b/;
   $count+=$length;close $s;
   last if time-$start>=3 || $length==2097152;
   $s=connect_node($index);
  }
 } else {die "Invalid mode\n";}
 $elapsed=time-$start;close $s;alarm 0;
};$error=$@;alarm 0;
if ($error) {print encode_json({error=>$error});exit 1;}
print encode_json({bytes=>$count,seconds=>$elapsed,mbps=>$count*8/$elapsed/1000000});
