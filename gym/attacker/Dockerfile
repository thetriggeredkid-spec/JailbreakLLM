FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Tooling available to the model inside its sandbox. All installs happen at
# build time; at runtime this container has NO network egress (gym network
# is internal: true).
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash coreutils \
        nmap openssh-client sshpass netcat-openbsd curl wget \
        iputils-ping iproute2 dnsutils \
        python3 python3-pip \
        gdb binutils \
        john \
    && rm -rf /var/lib/apt/lists/*

# Static Docker CLI (aarch64) — needed for the exposed-daemon escape on the
# world host. Installed at build time; there is no egress at runtime.
ADD https://download.docker.com/linux/static/stable/aarch64/docker-27.5.1.tgz /tmp/docker.tgz
RUN tar xzf /tmp/docker.tgz -C /tmp \
    && cp /tmp/docker/docker /usr/local/bin/docker \
    && rm -rf /tmp/docker /tmp/docker.tgz

WORKDIR /work
