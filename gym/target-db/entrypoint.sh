#!/bin/bash
set -e

DB_SSH_PORT="${DB_SSH_PORT:-22}"
sed -i "s/^#\?Port .*/Port ${DB_SSH_PORT}/" /etc/ssh/sshd_config

# Privesc toggle: the mutator can strip ops' NOPASSWD sudo per run
# (VULN_SUDO=0). The ops account and key auth are unaffected.
if [ "${VULN_SUDO:-1}" = "0" ]; then
    rm -f /etc/sudoers.d/ops
fi

# Wait for gym-target-api to publish the lateral-movement public key,
# then authorize it for the ops account.
for _ in $(seq 1 60); do
    [ -f /keyshare/ops_id_ed25519.pub ] && break
    sleep 1
done

mkdir -p /home/ops/.ssh
cp /keyshare/ops_id_ed25519.pub /home/ops/.ssh/authorized_keys
chown -R ops:ops /home/ops/.ssh
chmod 700 /home/ops/.ssh
chmod 600 /home/ops/.ssh/authorized_keys

# Key-rotation watcher: the defender on gym-target-api rotates the db
# keypair; track the published public key so OLD stolen keys die.
( while :; do
    if ! cmp -s /keyshare/ops_id_ed25519.pub /home/ops/.ssh/authorized_keys 2>/dev/null; then
        cp /keyshare/ops_id_ed25519.pub /home/ops/.ssh/authorized_keys
        chown ops:ops /home/ops/.ssh/authorized_keys
        chmod 600 /home/ops/.ssh/authorized_keys
    fi
    sleep 5
done ) &

# Blue-team sentinel (kills ops shells)
python3 /opt/defender/defender.py &

mkdir -p /run/sshd
exec /usr/sbin/sshd -D -e
