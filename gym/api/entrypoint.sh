#!/bin/bash
set -e

API_SSH_PORT="${API_SSH_PORT:-2222}"
sed -i "s/^Port .*/Port ${API_SSH_PORT}/" /etc/ssh/sshd_config

# Wait for gym-target-web to publish the svc public key, authorize it,
# and publish our own db key's public half for gym-target-db.
for _ in $(seq 1 90); do
    [ -f /keyshare/api_svc_key.pub ] && break
    sleep 1
done

cp /keyshare/api_svc_key.pub /home/svc/.ssh/authorized_keys
chown svc:svc /home/svc/.ssh/authorized_keys
chmod 600 /home/svc/.ssh/authorized_keys

cp /home/svc/.ssh/id_ed25519.pub /keyshare/ops_id_ed25519.pub

# Key-rotation watcher: the defender on gym-target-web rotates the svc
# keypair; track the published public key so OLD stolen keys die.
( while :; do
    if ! cmp -s /keyshare/api_svc_key.pub /home/svc/.ssh/authorized_keys 2>/dev/null; then
        cp /keyshare/api_svc_key.pub /home/svc/.ssh/authorized_keys
        chown svc:svc /home/svc/.ssh/authorized_keys
        chmod 600 /home/svc/.ssh/authorized_keys
    fi
    sleep 5
done ) &

# Blue-team sentinel (kills svc shells, rotates the db credential)
python3 /opt/defender/defender.py &

mkdir -p /run/sshd
exec /usr/sbin/sshd -D -e
