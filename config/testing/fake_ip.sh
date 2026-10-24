#!/usr/bin/env bash
# A stand-in for `ip -o -4 addr show` (config/tests_linux_scripts.py): FAKE_IPS lists this server's
# IPv4 addresses (default "127.0.0.1 192.168.1.10").
n=1
for address in ${FAKE_IPS:-127.0.0.1 192.168.1.10}; do
  printf '%s: eth%s    inet %s/24 brd 0.0.0.0 scope global eth%s\\       valid_lft forever preferred_lft forever\n' \
    "$n" "$n" "$address" "$n"
  n=$((n + 1))
done
