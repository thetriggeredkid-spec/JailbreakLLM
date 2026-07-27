#!/bin/bash
set -e

# Docker API on plaintext $DOCKERD_PORT, reachable only from the isolated
# world net.
DOCKERD_PORT="${DOCKERD_PORT:-2375}"
dockerd --host=tcp://0.0.0.0:${DOCKERD_PORT} --host=unix:///var/run/docker.sock \
    >/var/log/dockerd.log 2>&1 &

for _ in $(seq 1 90); do
    docker info >/dev/null 2>&1 && break
    sleep 1
done

docker load -i /opt/alpine.tar

wait
