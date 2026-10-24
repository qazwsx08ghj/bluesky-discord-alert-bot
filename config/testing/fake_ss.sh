#!/usr/bin/env bash
# A stand-in for `ss -Hltn "sport = :PORT"` (config/tests_linux_scripts.py): FAKE_PORTS_IN_USE lists
# "address:port" listeners, IPv4 or IPv6 (e.g. "0.0.0.0:8000 [::]:9000"); FAKE_SS_FAIL: ss is broken.
[ -n "${FAKE_SS_FAIL:-}" ] && exit 1
last="${*: -1}"
port="${last##*:}"
for listener in ${FAKE_PORTS_IN_USE:-}; do
  [ "${listener##*:}" = "$port" ] && echo "LISTEN 0      4096       $listener      *:*"
done
exit 0
