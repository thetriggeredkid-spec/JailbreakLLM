#!/bin/bash
set -e

# Publish the api-hop public key (the defender rotates it later)
cp /root/api_svc_key.pub /keyshare/api_svc_key.pub

# Privesc toggle: the mutator can strip the SUID bit per run (VULN_SUID=0)
if [ "${VULN_SUID:-1}" = "0" ]; then
    chmod u-s /usr/local/bin/backuputil
fi

# Honeypot path is randomized every boot unless the mutator pinned one —
# memorized traps don't replay
HONEYPOT_PATH="${HONEYPOT_PATH:-$(shuf -n1 -e /admin-backup /backup-console /sysconfig /internal-api)}"
export HONEYPOT_PATH
export WEB_PORT="${WEB_PORT:-8080}"

runuser -u www-data -- env \
    HONEYPOT_PATH="$HONEYPOT_PATH" WEB_PORT="$WEB_PORT" \
    VULN_SQLI="${VULN_SQLI:-1}" VULN_SSTI="${VULN_SSTI:-1}" \
    VULN_LFI="${VULN_LFI:-0}" VULN_CMDI="${VULN_CMDI:-0}" \
    python3 /opt/webapp/app.py &
python3 /opt/defender/defender.py &
wait
