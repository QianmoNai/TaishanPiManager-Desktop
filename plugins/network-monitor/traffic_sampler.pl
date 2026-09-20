#!/usr/bin/perl
# TSPI_MANAGER_MONITOR_V1
use strict;
use warnings;
use JSON::PP qw(encode_json decode_json);
use Time::HiRes qw(sleep);
use Fcntl qw(:flock);

my $dir = '/userdata/traffic-monitor';
my $live = '/tmp/tspi-traffic.json';
sub read_text { my ($p)=@_; open my $f,'<',$p or return ''; local $/; return <$f>; }
sub read_json { my ($p)=@_; my $v=eval { decode_json(read_text($p)) }; return ref($v) eq 'HASH' ? $v : {}; }
sub write_json {
    my ($p,$data)=@_;
    open my $f,'>',"$p.tmp.$$" or die "write $p: $!";
    print $f encode_json($data); close $f or die "close: $!";
    rename "$p.tmp.$$",$p or die "rename: $!";
}
sub sample {
    my ($state,$boot,$up,$raw)=@_;
    my $same=($state->{boot}//'') eq $boot;
    my $dt=$same ? $up-($state->{uptime}//$up) : 0;
    my %seen;
    for my $line (split /\n/,$raw) {
        next unless $line =~ /^\s*([\w.:-]+):\s*(\d+)\s+(.*)$/;
        my ($name,$rx,$rest)=($1,0+$2,$3); next if $name eq 'lo';
        my @v=split /\s+/,$rest; next if @v<8; my $tx=0+$v[7];
        my $old=$state->{interfaces}{$name}//{};
        my ($dr,$dt_bytes)=(0,0);
        if (exists $old->{rx}) {
            # On reboot/reset the new kernel counters start at zero. First-ever
            # observation is a baseline; never count traffic predating install.
            $dr=($same && $rx>=($old->{rx}//0)) ? $rx-$old->{rx} : $rx;
            $dt_bytes=($same && $tx>=($old->{tx}//0)) ? $tx-$old->{tx} : $tx;
        }
        $seen{$name}={rx=>$rx,tx=>$tx,total_rx=>($old->{total_rx}//0)+$dr,
            total_tx=>($old->{total_tx}//0)+$dt_bytes,
            rx_rate=>($same && $dt>0 && exists($old->{rx}) && $rx>=$old->{rx}) ? $dr/$dt : 0,
            tx_rate=>($same && $dt>0 && exists($old->{tx}) && $tx>=$old->{tx}) ? $dt_bytes/$dt : 0,
            present=>JSON::PP::true};
    }
    for my $name (keys %{$state->{interfaces}//{}}) {
        next if exists $seen{$name};
        $seen{$name}={%{$state->{interfaces}{$name}},rx_rate=>0,tx_rate=>0,present=>JSON::PP::false};
    }
    return {version=>2,boot=>$boot,uptime=>$up,started=>$state->{started}//time,
        updated=>time,interfaces=>\%seen};
}

unless (caller) {
    umask 0077;
    mkdir $dir unless -d $dir;
    open my $lock,'>',"$dir/daemon.lock" or die $!;
    flock($lock,LOCK_EX|LOCK_NB) or die "Already running\n";
    my $state=read_json("$dir/totals.json");
    die "Invalid persisted totals; restore backup instead of overwriting\n" if -s "$dir/totals.json" && !$state->{version};
    my $stop=0; $SIG{TERM}=sub {$stop=1}; $SIG{INT}=sub {$stop=1}; $SIG{HUP}=sub {$stop=1};
    my $boot=read_text('/proc/sys/kernel/random/boot_id'); chomp $boot;
    my $last_save=0;
    while (1) {
        my ($up)=split /\s+/,read_text('/proc/uptime');
        $state=sample($state,$boot,0+$up,read_text('/proc/net/dev'));
        write_json($live,$state);
        if ($stop || $up-$last_save>=60 || !$last_save) { write_json("$dir/totals.json",$state); $last_save=$up; }
        last if $stop;
        sleep 2;
    }
}
1;
