#!/usr/bin/perl
# TSPI_MANAGER_MONITOR_V1
# Compatibility entry point: network traffic snapshot only.
use strict;
open my $f, '<', '/tmp/tspi-traffic.json' or die "Start traffic monitor first\n";
print while <$f>;
