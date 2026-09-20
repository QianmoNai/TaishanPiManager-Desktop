"""Read standard ip output supported by both BusyBox and iproute2."""
import re


NETWORK_STATUS_COMMAND = "ip addr show && printf '\\n@@routes\\n' && ip route show"


def parse_network_status(text):
    addresses, _, routes = text.partition('@@routes')
    defaults = list(dict.fromkeys(re.findall(r'^default\b[^\n]*?\bdev\s+(\S+)', routes or text, re.M)))
    interfaces = []
    current = None
    for line in addresses.splitlines():
        match = re.match(r'^\d+:\s+([^:]+):\s+<([^>]*)>(.*)', line)
        if match:
            iface = match[1].split('@', 1)[0]
            current = None
            if iface == 'lo':
                continue
            flags = match[2].split(',')
            state = re.search(r'\bstate\s+(\S+)', match[3])
            link = state[1] if state else ('UP' if 'LOWER_UP' in flags else 'UNKNOWN' if 'UP' in flags else 'DOWN')
            current = {'interface': iface, 'state': link, 'ips': []}
            interfaces.append(current)
        elif current is not None:
            address = re.match(r'\s+inet\s+(\d+\.\d+\.\d+\.\d+/\d+)\b', line)
            if address:
                current['ips'].append(address[1])
    # BusyBox builds may only provide the compact `ip -br addr` form.
    if not interfaces:
        for line in addresses.splitlines():
            fields = line.split(None, 2)
            if len(fields) < 2 or fields[0] == 'default':
                continue
            iface, state = fields[0], fields[1]
            if iface == 'lo':
                continue
            ips = re.findall(r'\b\d+\.\d+\.\d+\.\d+/\d+\b', fields[2] if len(fields) > 2 else '')
            interfaces.append({'interface': iface, 'state': state, 'ips': ips})
    rows = []
    for entry in interfaces:
        iface = entry['interface']
        kind = 'USB 网卡' if iface.startswith(('eth', 'enx')) and iface != 'eth0' else ('Wi-Fi' if iface.startswith('wl') else '有线网卡')
        rows.append((iface, kind, entry['state'], ', '.join(entry['ips']) or '—', '是' if iface in defaults else ''))
    return rows, '、'.join(defaults)
