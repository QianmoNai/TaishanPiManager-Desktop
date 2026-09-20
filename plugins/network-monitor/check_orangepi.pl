#!/usr/bin/perl
# TSPI_MANAGER_MONITOR_V1
use strict;
use warnings;
use utf8;
use IO::Socket::INET;
use Encode qw(encode decode);
use Fcntl qw(O_RDWR O_NONBLOCK);
use Errno qw(EAGAIN EWOULDBLOCK);
use Time::HiRes qw(time);

binmode STDOUT, ':encoding(UTF-8)';
binmode STDERR, ':encoding(UTF-8)';
select((select(STDOUT), $| = 1)[0]);
select((select(STDERR), $| = 1)[0]);

my $orangepi_host = '192.168.1.158';
my $orangepi_web_port = 18080;
my $orangepi_ssh_port = 22;
my $router_ip = '192.168.1.1';
my $router_ssh_port = 22;
my $router_dns_port = 53;
my $router_luci_path = '/cgi-bin/luci';
my $timeout = 3;
my $json = 0;
my $lcd = '';
my $lcd_baud = 115200;
my $lcd_page_seconds = 5;
my $monitor = 0;
my $interval = 600;
my $ld06_device = '/dev/ttyS3';
my $ld06_baud = 230400;
my $ld06_timeout = 0.8;
my $ld06_enabled = 1;
my $LD06_RADAR_RANGE_MM = 1000;
my ($cpu_prev_total, $cpu_prev_idle);

my @LD06_CRC_TABLE;
{
    my $poly = 0x4d;
    for my $i (0..255) {
        my $crc = $i;
        for (0..7) {
            if ($crc & 0x80) {
                $crc = (($crc << 1) ^ $poly) & 0xFF;
            } else {
                $crc = ($crc << 1) & 0xFF;
            }
        }
        $LD06_CRC_TABLE[$i] = $crc;
    }
}

while (@ARGV) {
    my $arg = shift @ARGV;
    if ($arg eq '--host' || $arg eq '--orangepi-host') { $orangepi_host = shift @ARGV; }
    elsif ($arg eq '--web-port' || $arg eq '--orangepi-web-port') { $orangepi_web_port = int(shift @ARGV); }
    elsif ($arg eq '--ssh-port' || $arg eq '--orangepi-ssh-port') { $orangepi_ssh_port = int(shift @ARGV); }
    elsif ($arg eq '--router-ip') { $router_ip = shift @ARGV; }
    elsif ($arg eq '--router-ssh-port') { $router_ssh_port = int(shift @ARGV); }
    elsif ($arg eq '--router-dns-port') { $router_dns_port = int(shift @ARGV); }
    elsif ($arg eq '--timeout') { $timeout = shift @ARGV; }
    elsif ($arg eq '--json') { $json = 1; }
    elsif ($arg eq '--lcd' || $arg eq '--lcd-device') { $lcd = shift @ARGV; }
    elsif ($arg eq '--lcd-baud') { $lcd_baud = int(shift @ARGV); }
    elsif ($arg eq '--lcd-page-seconds') { $lcd_page_seconds = shift @ARGV; }
    elsif ($arg eq '--monitor') { $monitor = 1; }
    elsif ($arg eq '--interval') { $interval = int(shift @ARGV); }
    elsif ($arg eq '--ld06-device') { $ld06_device = shift @ARGV; }
    elsif ($arg eq '--ld06-baud') { $ld06_baud = int(shift @ARGV); }
    elsif ($arg eq '--ld06-timeout') { $ld06_timeout = shift @ARGV; }
    elsif ($arg eq '--no-ld06') { $ld06_enabled = 0; }
    elsif ($arg eq '-h' || $arg eq '--help') { usage(); exit 0; }
    else { die "Unknown argument: $arg\n"; }
}

sub usage {
    print <<"EOF";
Usage:
  check_orangepi [--json]
  check_orangepi --lcd /dev/ttyACM0
  check_orangepi --monitor --interval 600 --lcd /dev/ttyACM0 --lcd-page-seconds 5
  check_orangepi --orangepi-host 192.168.1.158 --router-ip 192.168.1.1 --timeout 5
  check_orangepi --no-ld06   (disable LD06 LiDAR page)

Checks both:
  Orange Pi RAG/NAS: SSH, Web, /api/healthz, /api/status, /api/sysinfo, home page
  QWRT router: LAN gateway, SSH, LuCI, DNS, HTTP internet, HTTPS internet

LD06 LiDAR (optional):
  --ld06-device PATH  Serial device (default: /dev/ttyS3)
  --ld06-baud N       Baud rate (default: 230400)
  --ld06-timeout SEC  Read timeout (default: 0.8)
  --no-ld06           Disable LD06 LiDAR page and reading

Exit codes:
  0 = Orange Pi and QWRT are both normal
  1 = Orange Pi abnormal
  2 = QWRT abnormal
  3 = both abnormal
EOF
}

sub ld06_crc8 {
    my (@bytes) = @_;
    my $crc = 0;
    for my $b (@bytes) {
        $crc = $LD06_CRC_TABLE[($crc ^ $b) & 0xff];
    }
    return $crc;
}

sub read_ld06_frame {
    my ($device, $baud, $timeout) = @_;
    my $FRAME_LEN = 47;
    my $HEADER    = 0x54;
    my $VER_LEN   = 0x2c;
    my $MAX_POINTS = 500;

    system('stty', '-F', $device, $baud, 'cs8', '-parenb', '-cstopb',
        '-ixon', '-ixoff', '-crtscts', '-echo', '-icanon', 'min', '0', 'time', '1', 'clocal', 'cread');

    sysopen(my $fh, $device, O_RDWR | O_NONBLOCK) or return { ok => 0, error => "open: $!" };
    binmode $fh, ':raw';

    my $buf = '';
    my $deadline = time + $timeout;

    my $first_hz = 0;
    my $first_start_deg = 0;
    my $first_timestamp = 0;
    my $last_end_deg = 0;
    my $global_closest_mm = 999999;
    my $global_closest_angle = 0;
    my @points;
    my $got_first = 0;

    while (time < $deadline && scalar(@points) < $MAX_POINTS) {
        my $chunk = '';
        my $n = sysread($fh, $chunk, 256);
        if (defined $n && $n > 0) {
            $buf .= $chunk;
        } elsif (!defined $n) {
            if (!($! == EAGAIN || $! == EWOULDBLOCK)) {
                close $fh;
                return { ok => 0, error => "read: $!" };
            }
        }

        while (length($buf) >= $FRAME_LEN) {
            my $idx = index($buf, chr($HEADER) . chr($VER_LEN));
            last if $idx < 0;
            last if length($buf) - $idx < $FRAME_LEN;

            my $frame = substr($buf, $idx, $FRAME_LEN);
            my @b = unpack("C*", $frame);
            $buf = substr($buf, $idx + $FRAME_LEN);

            my $calc_crc = ld06_crc8(@b[0..45]);
            next if $calc_crc != $b[46];

            my $speed_raw = $b[2] | ($b[3] << 8);
            my $start_raw = $b[4] | ($b[5] << 8);
            my $end_raw   = $b[42] | ($b[43] << 8);
            my $ts        = $b[44] | ($b[45] << 8);

            my $speed_deg_s = $speed_raw;
            my $hz          = $speed_deg_s / 360.0;
            my $start_deg   = $start_raw * 0.01;
            my $end_deg     = $end_raw * 0.01;

            if (!$got_first) {
                $first_hz = $hz;
                $first_start_deg = $start_deg;
                $first_timestamp = $ts;
                $got_first = 1;
            }
            $last_end_deg = $end_deg;

            my $delta = $end_deg - $start_deg;
            $delta += 360.0 if $delta < 0;

            for my $i (0..11) {
                my $off  = 6 + $i * 3;
                my $dist = $b[$off] | ($b[$off + 1] << 8);
                my $intensity = $b[$off + 2];
                next if $dist == 0;
                my $angle = $start_deg + ($delta * $i / 11.0);
                $angle -= 360.0 if $angle >= 360.0;

                push @points, {
                    angle     => $angle,
                    distance  => $dist,
                    intensity => $intensity,
                };

                if ($dist < $global_closest_mm) {
                    $global_closest_mm    = $dist;
                    $global_closest_angle = $angle;
                }
            }
        }

        select(undef, undef, undef, 0.02);
    }

    close $fh;

    if (!@points) {
        return { ok => 0, error => '无数据' };
    }

    return {
        ok             => 1,
        hz             => $first_hz,
        start_deg      => $first_start_deg,
        end_deg        => $last_end_deg,
        closest_mm     => $global_closest_mm < 999999 ? $global_closest_mm : 0,
        closest_angle  => int($global_closest_angle * 10 + 0.5) / 10,
        point_count    => scalar(@points),
        timestamp      => $first_timestamp,
        points         => \@points,
    };
}

sub add_result {
    my ($results, $name, $ok, $message, $details) = @_;
    push @$results, {
        name => $name,
        ok => $ok ? 1 : 0,
        message => $message,
        details => $details || {},
    };
}

sub tcp_check {
    my ($host, $port, $timeout) = @_;
    my $sock = IO::Socket::INET->new(
        PeerHost => $host,
        PeerPort => $port,
        Proto => 'tcp',
        Timeout => $timeout,
    );
    if ($sock) {
        close $sock;
        return (1, "$host:$port connected");
    }
    return (0, "$!");
}

sub http_request {
    my ($connect_host, $port, $path, $timeout, $host_header) = @_;
    $host_header = $connect_host unless defined $host_header && length($host_header);
    my $sock = IO::Socket::INET->new(
        PeerHost => $connect_host,
        PeerPort => $port,
        Proto => 'tcp',
        Timeout => $timeout,
    );
    return (0, undef, '', "connect failed: $!", '') unless $sock;
    $sock->timeout($timeout);
    binmode $sock;
    print $sock "GET $path HTTP/1.0\r\nHost: $host_header\r\nUser-Agent: check-lan-taishan/1.1\r\nAccept: */*\r\nConnection: close\r\n\r\n";
    my $raw = '';
    my $buf = '';
    while (1) {
        my $n = sysread($sock, $buf, 4096);
        last if !defined($n) || $n == 0;
        $raw .= $buf;
        last if length($raw) > 1024 * 1024;
    }
    close $sock;

    my ($head, $body) = split(/\r?\n\r?\n/, $raw, 2);
    $head = '' unless defined $head;
    $body = '' unless defined $body;
    my ($status) = $head =~ m{^HTTP/\S+\s+(\d+)}i;
    my $ok = defined($status) && $status >= 200 && $status < 400;
    return ($ok, $status, $head, '', decode('UTF-8', $body, Encode::FB_DEFAULT));
}

sub json_bool {
    my ($body, $key) = @_;
    return undef unless $body =~ /"\Q$key\E"\s*:\s*(true|false)/;
    return $1 eq 'true' ? 1 : 0;
}

sub json_string {
    my ($body, $key) = @_;
    return undef unless $body =~ /"\Q$key\E"\s*:\s*"([^"]*)"/;
    return $1;
}

sub json_number {
    my ($body, $key) = @_;
    return undef unless $body =~ /"\Q$key\E"\s*:\s*(-?\d+(?:\.\d+)?)/;
    return $1;
}

sub json_escape {
    my ($s) = @_;
    $s = '' unless defined $s;
    $s =~ s/\\/\\\\/g;
    $s =~ s/"/\\"/g;
    $s =~ s/\r/\\r/g;
    $s =~ s/\n/\\n/g;
    return $s;
}

sub dns_query_a {
    my ($server, $port, $name, $timeout) = @_;
    my $last_error = '';
    for my $attempt (1 .. 3) {
        my $id = int(rand(65535));
        my $packet = pack('n n n n n n', $id, 0x0100, 1, 0, 0, 0);
        for my $label (split(/\./, $name)) {
            $packet .= pack('C', length($label)) . $label;
        }
        $packet .= "\0" . pack('n n', 1, 1);

        my $sock = IO::Socket::INET->new(
            PeerHost => $server,
            PeerPort => $port,
            Proto => 'udp',
            Timeout => $timeout,
        );
        if (!$sock) {
            $last_error = 'udp socket failed';
            next;
        }
        $sock->send($packet);
        $sock->timeout($timeout);
        my $response = '';
        my $peer = $sock->recv($response, 1500);
        close $sock;
        if (!(defined($peer) && length($response) >= 12)) {
            $last_error = 'no dns response';
            next;
        }

        my ($rid, $flags, $qd, $an) = unpack('n n n n', substr($response, 0, 8));
        if ($rid != $id) {
            $last_error = 'dns transaction id mismatch';
            next;
        }
        if (($flags & 0x000f) != 0) {
            $last_error = 'dns response code not zero';
            next;
        }
        if ($an < 1) {
            $last_error = 'dns answer empty';
            next;
        }
        return (1, "resolved $name via $server");
    }
    return (0, $last_error || 'dns query failed');
}

sub command_exists {
    my ($name) = @_;
    system("command -v '$name' >/dev/null 2>&1");
    return $? == 0;
}

sub shell_quote {
    my ($s) = @_;
    $s =~ s/'/'"'"'/g;
    return "'$s'";
}

sub https_check_wget {
    my ($url, $timeout) = @_;
    my ($tcp_ok, $tcp_msg) = tcp_check('www.fzu.edu.cn', 443, $timeout);
    return (0, "tcp 443 failed: $tcp_msg") unless $tcp_ok;
    return (1, 'https tcp reachable; wget not found for TLS GET') unless command_exists('wget');
    my $cmd = 'wget -q -O - --timeout=' . int($timeout) . ' --tries=1 ' . shell_quote($url) . ' >/tmp/check_qwrt_https.out 2>/tmp/check_qwrt_https.err';
    system($cmd);
    return (1, 'https ok') if $? == 0;
    my $err = '';
    if (open(my $fh, '<', '/tmp/check_qwrt_https.err')) {
        local $/;
        $err = <$fh>;
        close $fh;
    }
    $err =~ s/\s+/ /g;
    $err = substr($err, 0, 120);
    return (1, 'https tcp reachable; wget has no HTTPS support') if $err =~ /not an http or ftp url/i;
    return (0, $err || 'https request failed');
}

sub check_orangepi {
    my @results;
    my ($ssh_ok, $ssh_msg) = tcp_check($orangepi_host, $orangepi_ssh_port, $timeout);
    add_result(\@results, 'ssh_tcp', $ssh_ok, $ssh_ok ? "SSH port reachable: $orangepi_host:$orangepi_ssh_port" : "SSH port not reachable: $orangepi_host:$orangepi_ssh_port $ssh_msg");

    my ($web_ok, $web_msg) = tcp_check($orangepi_host, $orangepi_web_port, $timeout);
    add_result(\@results, 'web_tcp', $web_ok, $web_ok ? "Web port reachable: $orangepi_host:$orangepi_web_port" : "Web port not reachable: $orangepi_host:$orangepi_web_port $web_msg");

    if ($web_ok) {
        my ($ok, $status, $head, $err, $body) = http_request($orangepi_host, $orangepi_web_port, '/api/healthz', $timeout);
        my $health_ok = $ok && defined(json_bool($body, 'ok')) && json_bool($body, 'ok');
        add_result(\@results, 'healthz', $health_ok, $health_ok ? '/api/healthz ok' : "/api/healthz failed: HTTP " . (defined($status) ? $status : 'none'));

        ($ok, $status, $head, $err, $body) = http_request($orangepi_host, $orangepi_web_port, '/api/status', $timeout);
        my $embedding = json_string($body, 'embedding_backend');
        my $reranker = json_bool($body, 'reranker_enabled');
        my $active = json_number($body, 'active_embeddings');
        my $db = json_string($body, 'db');
        my $status_ok = $ok && defined($embedding) && defined($reranker) && defined($active) && defined($db);
        add_result(\@results, 'status', $status_ok,
            $status_ok ? "/api/status ok, embedding=$embedding, reranker=" . ($reranker ? 'true' : 'false') . ", active_embeddings=$active"
                       : "/api/status missing expected fields");

        ($ok, $status, $head, $err, $body) = http_request($orangepi_host, $orangepi_web_port, '/api/sysinfo', $timeout);
        my $sysinfo_ok = $ok && $body =~ /"cpu_/ && $body =~ /"mem_/ && $body =~ /"disk_/;
        add_result(\@results, 'sysinfo', $sysinfo_ok, $sysinfo_ok ? '/api/sysinfo ok' : '/api/sysinfo missing CPU/memory/disk fields');

        ($ok, $status, $head, $err, $body) = http_request($orangepi_host, $orangepi_web_port, '/', $timeout);
        my $home_ok = $ok && ($body =~ /qianmo/i || $body =~ /知识库/ || $body =~ /nas/i || $body =~ /rag/i);
        add_result(\@results, 'home', $home_ok, $home_ok ? '/ reachable, page keyword found' : '/ failed or no expected keyword');
    }

    my ($code, $conclusion, $suggestion) = decide_orangepi(\@results);
    return { name => 'orangepi', target => $orangepi_host, status => $code == 0 ? 'ORANGEPI_OK' : 'ORANGEPI_FAIL', code => $code, conclusion => $conclusion, suggestion => $suggestion, checks => \@results };
}

sub decide_orangepi {
    my ($results) = @_;
    my %ok = map { $_->{name} => $_->{ok} } @$results;
    my $ssh_ok = $ok{ssh_tcp} || 0;
    my $web_ok = $ok{web_tcp} || 0;
    my $health_ok = $ok{healthz} || 0;
    my $status_ok = $ok{status} || 0;
    my $sysinfo_ok = $ok{sysinfo} || 0;
    my $home_ok = $ok{home} || 0;

    return (3, '服务异常', '疑似 IP 变化或设备离线：SSH 和 Web 端口都不可达') if !$ssh_ok && !$web_ok;
    if ($web_ok && $health_ok && $status_ok && $sysinfo_ok && $home_ok) {
        return $ssh_ok
            ? (0, '香橙派 RAG/NAS 服务正常', '全部关键项正常')
            : (2, '服务正常但 SSH 异常', 'Web 服务正常，但 SSH 端口不可达');
    }
    return (1, '设备在线但 RAG/NAS Web 服务异常', '建议检查 local-rag-node 是否启动') if $ssh_ok && !$web_ok;
    return (1, 'Web 端口可达但健康检查失败', '建议检查 local-rag-node 日志或重启服务') if $web_ok && !$health_ok;
    return (4, '返回内容异常', '服务有响应，但返回内容不符合预期');
}

sub check_router {
    my @results;
    my ($gateway_ok, $gateway_msg) = tcp_check($router_ip, 80, $timeout);
    if (!$gateway_ok) {
        ($gateway_ok, $gateway_msg) = tcp_check($router_ip, $router_ssh_port, $timeout);
    }
    add_result(\@results, 'gateway_reachable', $gateway_ok, $gateway_ok ? "gateway reachable: $router_ip" : "gateway not reachable: $router_ip $gateway_msg");

    my ($ssh_ok, $ssh_msg) = tcp_check($router_ip, $router_ssh_port, $timeout);
    add_result(\@results, 'ssh_open', $ssh_ok, $ssh_ok ? "ssh port open: $router_ssh_port" : "ssh port closed: $router_ssh_port $ssh_msg");

    my ($http_ok, $status, $head, $err, $body) = http_request($router_ip, 80, $router_luci_path, $timeout);
    my $luci_ok = (defined($status) && $status == 200) || (defined($status) && $status == 403 && $head =~ /X-LuCI-Login-Required:\s*yes/i);
    my $luci_msg = $luci_ok
        ? ($status == 403 ? 'luci reachable: login required' : 'luci reachable: http 200')
        : 'luci not normal: HTTP ' . (defined($status) ? $status : 'none');
    add_result(\@results, 'luci_ok', $luci_ok, $luci_msg);

    my ($dns_ok, $dns_msg) = dns_query_a($router_ip, $router_dns_port, 'www.baidu.com', $timeout);
    add_result(\@results, 'dns_ok', $dns_ok, $dns_ok ? 'dns resolve via router' : "dns resolve failed via router: $dns_msg");

    ($http_ok, $status, $head, $err, $body) = http_request('www.baidu.com', 80, '/', $timeout, 'www.baidu.com');
    my $portal = ($body =~ /eportal\/index\.jsp/i || $body =~ /wlanuserip/i || $head =~ /eportal\/index\.jsp/i) ? 1 : 0;
    my $internet_http_ok = $http_ok && !$portal;
    my $http_msg = $portal ? 'http internet redirected to eportal' : ($internet_http_ok ? 'http internet ok' : 'http internet failed');
    add_result(\@results, 'http_ok', $internet_http_ok, $http_msg, { portal_detected => $portal });

    my ($https_ok, $https_msg) = https_check_wget('https://www.fzu.edu.cn/', $timeout);
    my $https_line = $https_ok
        ? ($https_msg eq 'https ok' ? 'https internet ok' : "https internet ok ($https_msg)")
        : "https internet failed: $https_msg";
    add_result(\@results, 'https_ok', $https_ok, $https_line);

    my ($status_name, $suggestion) = decide_router(\@results, $portal);
    return { name => 'router', router_ip => $router_ip, status => $status_name, conclusion => "QWRT 状态: $status_name", suggestion => $suggestion, portal_detected => $portal, checks => \@results };
}

sub result_ok {
    my ($results, $name) = @_;
    for my $r (@$results) {
        return $r->{ok} if $r->{name} eq $name;
    }
    return 0;
}

sub decide_router {
    my ($results, $portal) = @_;
    my $gateway = result_ok($results, 'gateway_reachable');
    my $ssh = result_ok($results, 'ssh_open');
    my $luci = result_ok($results, 'luci_ok');
    my $dns = result_ok($results, 'dns_ok');
    my $http = result_ok($results, 'http_ok');
    my $https = result_ok($results, 'https_ok');

    return ('PORTAL_REQUIRED', '外网 HTTP 被校园网认证页接管，需要重新认证') if $portal;
    return ('DNS_FAIL', '路由器 DNS/dnsmasq/AdGuard 链路异常') if $gateway && !$dns;
    return ('LUCI_FAIL', '能访问路由器但 LuCI 管理面板异常') if $gateway && !$luci && ($http || $https);
    return ('SSH_FAIL', '路由器可用但 SSH 不通，检查 dropbear 或防火墙') if $gateway && !$ssh && ($http || $https);
    return ('ROUTER_OK', '路由器、DNS、外网都正常') if $gateway && $ssh && $luci && $dns && $http && $https;
    return ('LAN_ONLY', '能连路由器，但外网异常，检查 WAN、校园网认证或上游网络') if $gateway && (!$http || !$https);
    return ('LAN_ONLY', '当前设备可能没有连在这个 QWRT LAN/WiFi 下') if !$gateway;
    return ('LAN_ONLY', '路由器状态异常');
}

sub print_section {
    my ($title, $target, $checks, $suggestion, $conclusion) = @_;
    print "== $title: $target ==\n";
    for my $r (@$checks) {
        print (($r->{ok} ? '[OK] ' : '[FAIL] ') . "$r->{message}\n");
    }
    print "建议: $suggestion\n";
    print "结论: $conclusion\n\n";
}

sub print_combined_json {
    my ($orange, $router, $exit_code) = @_;
    print "{\n";
    print qq{  "exit_code": $exit_code,\n};
    print qq{  "orangepi": } . section_json($orange) . ",\n";
    print qq{  "router": } . section_json($router) . "\n";
    print "}\n";
}

sub section_json {
    my ($section) = @_;
    my $target_key = $section->{name} eq 'router' ? 'router_ip' : 'target';
    my $target_val = $section->{$target_key} || $section->{target} || '';
    my $s = "{\n";
    $s .= qq{    "$target_key": "} . json_escape($target_val) . qq{",\n};
    $s .= qq{    "status": "} . json_escape($section->{status}) . qq{",\n};
    $s .= qq{    "conclusion": "} . json_escape($section->{conclusion}) . qq{",\n};
    $s .= qq{    "suggestion": "} . json_escape($section->{suggestion}) . qq{",\n};
    $s .= qq{    "checks": [\n};
    my $checks = $section->{checks};
    for my $i (0 .. $#$checks) {
        my $r = $checks->[$i];
        $s .= "      {\n";
        $s .= qq{        "name": "} . json_escape($r->{name}) . qq{",\n};
        $s .= qq{        "ok": } . ($r->{ok} ? 'true' : 'false') . qq{,\n};
        $s .= qq{        "message": "} . json_escape($r->{message}) . qq{"\n};
        $s .= "      }" . ($i == $#$checks ? "\n" : ",\n");
    }
    $s .= "    ]\n";
    $s .= "  }";
    return $s;
}

sub lcd_cmd {
    my ($fh, $cmd) = @_;
    print $fh encode('UTF-8', $cmd . "\n");
    select(undef, undef, undef, 0.08);
}

sub lcd_short {
    my ($text, $len) = @_;
    $len ||= 18;
    $text =~ s/[\r\n]/ /g;
    return substr($text, 0, $len);
}

sub lcd_page {
    my ($fh, $title, $ok, $lines) = @_;
    lcd_cmd($fh, 'CLEAR');
    lcd_cmd($fh, 'LINEC 0 CYAN ' . lcd_short($title, 18));
    my $color = $ok ? 'GREEN' : 'RED';
    lcd_cmd($fh, "LINEC 1 $color " . ($ok ? '状态正常' : '状态异常'));
    my $line_no = 2;
    for my $line (@$lines) {
        last if $line_no > 4;
        lcd_cmd($fh, 'LINEC ' . $line_no . ' WHITE ' . lcd_short($line, 18));
        $line_no++;
    }
    lcd_cmd($fh, 'BRIGHT 80');
}

sub lcd_begin_page {
    my ($fh, $cache, $page_name) = @_;
    if (!defined($cache->{page}) || $cache->{page} ne $page_name) {
        lcd_cmd($fh, 'CLEAR');
        $cache->{page} = $page_name;
        $cache->{lines} = {};
    }
}

sub lcd_line_cached {
    my ($fh, $cache, $line_no, $color, $text) = @_;
    my $short = lcd_short($text, 18);
    my $cmd = "LINEC $line_no $color $short";
    return if defined($cache->{lines}{$line_no}) && $cache->{lines}{$line_no} eq $cmd;
    lcd_cmd($fh, $cmd);
    $cache->{lines}{$line_no} = $cmd;
}

sub lcd_bright_cached {
    my ($fh, $cache, $value) = @_;
    $value ||= 80;
    return if defined($cache->{bright}) && $cache->{bright} == $value;
    lcd_cmd($fh, "BRIGHT $value");
    $cache->{bright} = $value;
}

sub lcd_page_cached {
    my ($fh, $cache, $page_name, $title, $ok, $lines) = @_;
    lcd_begin_page($fh, $cache, $page_name);
    lcd_line_cached($fh, $cache, 0, 'CYAN', $title);
    lcd_line_cached($fh, $cache, 1, $ok ? 'GREEN' : 'RED', $ok ? '状态正常' : '状态异常');
    my $line_no = 2;
    for my $line (@$lines) {
        last if $line_no > 4;
        lcd_line_cached($fh, $cache, $line_no, 'WHITE', $line);
        $line_no++;
    }
    while ($line_no <= 4) {
        lcd_line_cached($fh, $cache, $line_no, 'WHITE', '');
        $line_no++;
    }
    lcd_bright_cached($fh, $cache, 80);
}

sub lcd_cmd_raw {
    my ($fh, $cmd) = @_;
    print $fh encode('UTF-8', $cmd . "\n");
}

sub ld06_radar_static {
    my ($fh) = @_;
    my $cx = 120;
    my $cy = 72;
    my $radius = 48;
    my $pi = 3.14159265358979;

    lcd_cmd_raw($fh, sprintf('RECT %d %d %d %d WHITE', $cx - $radius, $cy, $radius * 2, 1));
    lcd_cmd_raw($fh, sprintf('RECT %d %d %d %d WHITE', $cx, $cy - $radius, 1, $radius * 2));

    my @ring_colors = ('CYAN', 'YELLOW');
    my $ring_idx = 0;
    for my $r_factor (0.5, 1.0) {
        my $ring_r = int($radius * $r_factor);
        my $ring_color = $ring_colors[$ring_idx++];
        for (my $deg = 0; $deg < 360; $deg += 20) {
            my $rad = $deg * $pi / 180.0;
            my $px = int($cx + $ring_r * sin($rad) + 0.5);
            my $py = int($cy - $ring_r * cos($rad) + 0.5);
            lcd_cmd_raw($fh, sprintf('RECT %d %d 2 2 %s', $px - 1, $py - 1, $ring_color));
        }
    }
}

sub lcd_text_cached {
    my ($fh, $cache, $key, $x, $y, $sz, $color, $text) = @_;
    my $texts = $cache->{ld06_texts} || {};
    $cache->{ld06_texts} = $texts;

    my $entry = $texts->{$key};
    return if $entry && $entry->{text} eq $text && $entry->{color} eq $color;

    if ($entry) {
        my $char_w = $sz == 2 ? 12 : 8;
        my $char_h = $sz == 2 ? 18 : 10;
        my $w = length($entry->{text}) * $char_w;
        lcd_cmd_raw($fh, "RECT $x $y $w $char_h BLACK");
    }

    lcd_cmd_raw($fh, "TEXT $x $y $color $sz $text");
    $texts->{$key} = { text => $text, color => $color };
}

sub ld06_radar_update_points {
    my ($fh, $cache, $new_cells) = @_;
    my $old_cells = $cache->{ld06_cells} || {};

    for my $key (keys %$old_cells) {
        next if exists $new_cells->{$key} && $new_cells->{$key} eq $old_cells->{$key};
        my ($px, $py) = split(/,/, $key);
        lcd_cmd_raw($fh, sprintf('RECT %d %d 3 3 BLACK', $px - 1, $py - 1));
    }

    for my $key (keys %$new_cells) {
        my $color = $new_cells->{$key};
        next if exists $old_cells->{$key} && $old_cells->{$key} eq $color;
        my ($px, $py) = split(/,/, $key);
        lcd_cmd_raw($fh, sprintf('RECT %d %d 3 3 %s', $px - 1, $py - 1, $color));
    }

    $cache->{ld06_cells} = $new_cells;
}

sub lcd_ld06_page {
    my ($fh, $cache, $data) = @_;

    my $is_ok = $data && $data->{ok};
    my $state = $is_ok ? 'ok' : 'err';

    my $need_full = !defined($cache->{ld06_state}) || $cache->{ld06_state} ne $state;
    if ($need_full) {
        lcd_cmd_raw($fh, 'CLEAR');
        $cache->{ld06_state} = $state;
        $cache->{ld06_static_drawn} = 0;
        $cache->{ld06_texts} = {};
        $cache->{ld06_cells} = {};
        $cache->{ld06_sig} = undef;
    }

    my $data_sig = '';
    if ($is_ok) {
        $data_sig = join(',', $data->{hz}, $data->{closest_mm},
            int($data->{closest_angle}), $data->{point_count},
            $data->{timestamp}, int($data->{start_deg} * 100), int($data->{end_deg} * 100));
    } else {
        $data_sig = 'err:' . ($data && $data->{error} ? $data->{error} : 'unknown');
    }
    return if defined($cache->{ld06_sig}) && $cache->{ld06_sig} eq $data_sig && !$need_full;

    if (!$is_ok) {
        lcd_cmd_raw($fh, 'TEXT 10 10 RED 2 LD06 RADAR');
        lcd_cmd_raw($fh, 'TEXT 10 40 RED 1 NO DATA');
        my $dev_short = $ld06_device;
        $dev_short = substr($dev_short, 0, 18);
        lcd_cmd_raw($fh, "TEXT 10 60 WHITE 1 $dev_short");
        my $err = ($data && $data->{error}) ? $data->{error} : 'unknown';
        $err = substr($err, 0, 18);
        lcd_cmd_raw($fh, "TEXT 10 80 WHITE 1 $err");
        lcd_cmd_raw($fh, 'BRIGHT 80');
        select(undef, undef, undef, 0.15);
        $cache->{ld06_sig} = $data_sig;
        return;
    }

    if (!$cache->{ld06_static_drawn}) {
        ld06_radar_static($fh);
        $cache->{ld06_static_drawn} = 1;
    }

    my $title = sprintf('LD06 RADAR %.1fHz', $data->{hz});
    lcd_text_cached($fh, $cache, 'title', 5, 2, 2, 'CYAN', $title);

    my $dist_label = sprintf('NEAR %dmm %.0fdeg', $data->{closest_mm}, $data->{closest_angle});
    lcd_text_cached($fh, $cache, 'near', 5, 122, 1, 'WHITE', $dist_label);

    my $max_dist = $LD06_RADAR_RANGE_MM;
    lcd_text_cached($fh, $cache, 'range', 200, 122, 1, 'WHITE', '1m');

    my $cx = 120;
    my $cy = 72;
    my $radius = 48;
    my $pi = 3.14159265358979;

    my %new_cells;
    my $point_limit = 150;
    my $count = 0;
    for my $p (@{$data->{points}}) {
        last if $count >= $point_limit;
        my $dist = $p->{distance};
        my $angle = $p->{angle};
        next if $dist <= 0 || $dist > $LD06_RADAR_RANGE_MM;
        $count++;

        my $norm = $dist / $max_dist;
        my $r = $norm * $radius;
        my $rad = $angle * $pi / 180.0;
        my $px = int($cx + $r * sin($rad) + 0.5);
        my $py = int($cy - $r * cos($rad) + 0.5);

        my $color = 'GREEN';
        if ($norm < 0.20) {
            $color = 'RED';
        } elsif ($norm < 0.40) {
            $color = 'YELLOW';
        } elsif ($norm < 0.65) {
            $color = 'CYAN';
        }
        $new_cells{"$px,$py"} = $color;
    }

    ld06_radar_update_points($fh, $cache, \%new_cells);

    lcd_cmd_raw($fh, 'BRIGHT 80');
    select(undef, undef, undef, 0.15);

    $cache->{ld06_sig} = $data_sig;
}

sub lcd_open {
    my ($device, $baud) = @_;
    system('stty', '-F', $device, $baud, 'cs8', '-parenb', '-cstopb',
        '-ixon', '-ixoff', '-crtscts', '-echo', '-icanon', 'min', '0', 'time', '0', 'clocal', 'cread');
    sysopen(my $fh, $device, O_RDWR | O_NONBLOCK) or do {
        warn "[WARN] LCD open failed: $device: $!\n";
        return undef;
    };
    binmode $fh, ':raw';
    my $old = select($fh);
    $| = 1;
    select($old);
    return $fh;
}

sub poll_lcd_button {
    my ($fh, $buf_ref) = @_;
    my $pressed = 0;
    while (1) {
        my $chunk = '';
        my $n = sysread($fh, $chunk, 256);
        if (defined $n && $n > 0) {
            $$buf_ref .= decode('UTF-8', $chunk, Encode::FB_DEFAULT);
            $$buf_ref = substr($$buf_ref, -512) if length($$buf_ref) > 1024;
            next;
        }
        last if defined $n;
        last if $! == EAGAIN || $! == EWOULDBLOCK;
        warn "[WARN] LCD read failed: $!\n";
        last;
    }

    $$buf_ref =~ s/\r/\n/g;
    while ($$buf_ref =~ s/^([^\n]*)\n//) {
        my $line = $1;
        $line =~ s/^\s+|\s+$//g;
        next if $line eq '' || $line eq 'OK' || $line =~ /^ERR\b/;
        if ($line eq 'BUTTON GPIO0 PRESSED') {
            $pressed = 1;
        } else {
            print "[LCD] $line\n";
        }
    }
    return $pressed;
}

sub wait_lcd_or_button {
    my ($fh, $buf_ref, $seconds) = @_;
    my $deadline = time + $seconds;
    while (time < $deadline) {
        return 1 if poll_lcd_button($fh, $buf_ref);
        my $left = $deadline - time;
        my $step = $left < 0.2 ? $left : 0.2;
        select(undef, undef, undef, $step) if $step > 0;
    }
    return poll_lcd_button($fh, $buf_ref);
}

sub monitor_lcd_loop {
    my ($device, $baud, $seconds, $interval) = @_;
    my ($orange, $router, $exit_code);
    my $next_check = 0;
    my $max_page = $ld06_enabled ? 3 : 2;
    my $page = 0;
    my $page_entered = -1;
    my $ld06_next_refresh = 0;
    my $lcd_buffer = '';
    my %lcd_cache = ();
    my $fh;
    my $ld06_data;

    while (1) {
        if (!$fh) {
            $fh = lcd_open($device, $baud);
            if (!$fh) {
                select(undef, undef, undef, 5);
                next;
            }
            $page = 0;
            $page_entered = -1;
            %lcd_cache = ();
        }

        if (time >= $next_check || !defined($orange)) {
            ($orange, $router, $exit_code) = run_checks();
            my $now = scalar localtime;
            print "\n[$now] refreshed checks, next interval=${interval}s\n";
            print_human($orange, $router, $exit_code);
            $next_check = time + $interval;
        }

        if ($page_entered != $page) {
            $page_entered = $page;
            $ld06_next_refresh = 0 if $page == 3;
            %lcd_cache = ();
            my $now = scalar localtime;
            print "[$now] LCD page=$page (0=local 1=orangepi 2=qwrt 3=ld06)\n";
        }

        if ($page == 0) {
            local_status_page($fh, \%lcd_cache);
        } elsif ($page == 1) {
            my $orange_ok = $orange->{status} eq 'ORANGEPI_OK';
            lcd_page_cached($fh, \%lcd_cache, 'page_orangepi', '香橙派检测', $orange_ok, [
                $orange->{status},
                $orange_ok ? 'SSH WEB OK' : $orange->{suggestion},
                $orange->{conclusion},
            ]);
        } elsif ($page == 2) {
            my $router_ok = $router->{status} eq 'ROUTER_OK';
            lcd_page_cached($fh, \%lcd_cache, 'page_router', 'QWRT路由检测', $router_ok, [
                $router->{status},
                $router_ok ? 'DNS WEB OK' : $router->{suggestion},
                $router->{router_ip},
            ]);
        } elsif ($page == 3 && $ld06_enabled) {
            if (time >= $ld06_next_refresh) {
                $ld06_data = read_ld06_frame($ld06_device, $ld06_baud, $ld06_timeout);
                my $refresh_seconds = $seconds > 0 ? $seconds : 1;
                $ld06_next_refresh = time + $refresh_seconds;
            }
            lcd_ld06_page($fh, \%lcd_cache, $ld06_data);
        }

        my $wait = $page == 0 ? 1.0 : 0.5;

        if (wait_lcd_or_button($fh, \$lcd_buffer, $wait)) {
            my $old = $page;
            $page = ($page + 1) % ($max_page + 1);
            $page_entered = -1;
            my $now = scalar localtime;
            print "[$now] LCD button pressed, page=$old -> $page\n";
            next;
        }
    }
}

sub read_first_line {
    my ($path) = @_;
    open(my $fh, '<', $path) or return '';
    my $line = <$fh>;
    close $fh;
    $line = '' unless defined $line;
    $line =~ s/^\s+|\s+$//g;
    return $line;
}

sub local_cpu_percent {
    my $line = read_first_line('/proc/stat');
    return 'CPU --' unless $line =~ /^cpu\s+(.+)$/;
    my @v = split(/\s+/, $1);
    my $idle = ($v[3] || 0) + ($v[4] || 0);
    my $total = 0;
    $total += $_ for @v;
    my $pct = 0;
    if (defined $cpu_prev_total && $total > $cpu_prev_total) {
        my $dt = $total - $cpu_prev_total;
        my $di = $idle - $cpu_prev_idle;
        $pct = int((100 * ($dt - $di) / $dt) + 0.5) if $dt > 0;
    }
    $cpu_prev_total = $total;
    $cpu_prev_idle = $idle;
    return "CPU ${pct}%";
}

sub local_mem_line {
    my (%m, $line);
    open(my $fh, '<', '/proc/meminfo') or return 'MEM --';
    while ($line = <$fh>) {
        $m{$1} = $2 if $line =~ /^(MemTotal|MemAvailable):\s+(\d+)/;
    }
    close $fh;
    return 'MEM --' unless $m{MemTotal};
    my $used = int(($m{MemTotal} - ($m{MemAvailable} || 0)) / 1024);
    my $total = int($m{MemTotal} / 1024);
    return "MEM ${used}/${total}M";
}

sub local_disk_line {
    my $line = '';
    open(my $fh, '-|', 'df -h /userdata 2>/dev/null || df -h /') or return 'DISK --';
    while (my $l = <$fh>) {
        next if $l =~ /^Filesystem/;
        $line = $l;
        last;
    }
    close $fh;
    my @f = split(/\s+/, $line);
    return @f >= 5 ? "DISK $f[4] $f[3] free" : 'DISK --';
}

sub local_ip_line {
    my $ip = '';
    open(my $fh, '-|', 'ifconfig wlan0 2>/dev/null || ifconfig 2>/dev/null') or return 'IP --';
    while (my $line = <$fh>) {
        if ($line =~ /inet addr:([0-9.]+)/ || $line =~ /inet\s+([0-9.]+)/) {
            $ip = $1;
            last if $ip ne '127.0.0.1';
        }
    }
    close $fh;
    return $ip ? "IP $ip" : 'IP --';
}

sub local_temp_line {
    for my $path (glob('/sys/class/thermal/thermal_zone*/temp')) {
        my $raw = read_first_line($path);
        next unless $raw =~ /^\d+$/;
        my $c = $raw > 1000 ? int($raw / 1000) : int($raw);
        return "TEMP ${c}C";
    }
    my $load = read_first_line('/proc/loadavg');
    my ($l1) = split(/\s+/, $load);
    return $l1 ? "LOAD $l1" : 'LOAD --';
}

sub local_status_page {
    my ($fh, $cache) = @_;
    lcd_begin_page($fh, $cache, 'local_status');
    lcd_line_cached($fh, $cache, 0, 'CYAN', '泰山派状态');
    lcd_line_cached($fh, $cache, 1, 'GREEN', local_cpu_percent());
    lcd_line_cached($fh, $cache, 2, 'WHITE', local_mem_line());
    lcd_line_cached($fh, $cache, 3, 'WHITE', local_disk_line());
    lcd_line_cached($fh, $cache, 4, 'YELLOW', local_ip_line() . ' ' . local_temp_line());
    lcd_bright_cached($fh, $cache, 80);
}

sub write_lcd_pages {
    my ($device, $baud, $orange, $router, $seconds) = @_;
    my $fh = lcd_open($device, $baud);
    return unless $fh;
    my $lcd_buffer = '';
    my $orange_ok = $orange->{status} eq 'ORANGEPI_OK';
    my $router_ok = $router->{status} eq 'ROUTER_OK';
    lcd_page($fh, '香橙派检测', $orange_ok, [
        $orange->{status},
        $orange_ok ? 'SSH WEB OK' : $orange->{suggestion},
        $orange->{conclusion},
    ]);
    wait_lcd_or_button($fh, \$lcd_buffer, $seconds);
    lcd_page($fh, 'QWRT路由检测', $router_ok, [
        $router->{status},
        $router_ok ? 'DNS WEB OK' : $router->{suggestion},
        $router->{router_ip},
    ]);
    wait_lcd_or_button($fh, \$lcd_buffer, $seconds);
    if ($ld06_enabled) {
        my $ld06_data = read_ld06_frame($ld06_device, $ld06_baud, $ld06_timeout);
        my %cache = ();
        lcd_ld06_page($fh, \%cache, $ld06_data);
        wait_lcd_or_button($fh, \$lcd_buffer, $seconds);
    }
    close $fh;
}

sub run_checks {
    my $orange = check_orangepi();
    my $router = check_router();
    my $orange_ok = $orange->{status} eq 'ORANGEPI_OK';
    my $router_ok = $router->{status} eq 'ROUTER_OK';
    my $exit_code = $orange_ok && $router_ok ? 0 : (!$orange_ok && !$router_ok ? 3 : (!$orange_ok ? 1 : 2));
    return ($orange, $router, $exit_code);
}

sub print_human {
    my ($orange, $router, $exit_code) = @_;
    print_section('香橙派', $orange->{target}, $orange->{checks}, $orange->{suggestion}, $orange->{conclusion});
    print_section('QWRT 路由器', $router->{router_ip}, $router->{checks}, $router->{suggestion}, $router->{conclusion});
    print 'RESULT: ' . ($exit_code == 0 ? 'ALL_OK' : ($exit_code == 1 ? 'ORANGEPI_FAIL' : ($exit_code == 2 ? 'QWRT_FAIL' : 'BOTH_FAIL'))) . "\n";
}

if ($monitor) {
    die "--monitor requires --lcd /dev/ttyACM0 for continuous screen switching\n" unless $lcd;
    monitor_lcd_loop($lcd, $lcd_baud, $lcd_page_seconds, $interval);
}

my ($orange, $router, $exit_code) = run_checks();

if ($json) {
    print_combined_json($orange, $router, $exit_code);
} else {
    print_human($orange, $router, $exit_code);
}

write_lcd_pages($lcd, $lcd_baud, $orange, $router, $lcd_page_seconds) if $lcd;
exit $exit_code;
