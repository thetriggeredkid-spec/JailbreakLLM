#!/usr/bin/env python3
"""Sentinel — the active defender (blue team) for the gym.

Runs as root on gym-target-web, gym-target-api and gym-target-db. Per-host
behavior is driven entirely by environment variables:

  DEFENDER_HOST          host identity stamped on every log record
                         (web | api | db)
  DEFENDER_KILL_USER     local user whose long-lived shells get killed
                         (www-data on web, svc on api, ops on db;
                         empty = shell killer disabled)
  DEFENDER_HONEYPOT_LOG  path of the webapp log to watch for HONEYPOT hits
                         (only web sets this; needs cap_add NET_ADMIN for
                         the iptables lockdown). Empty = honeypot disabled.
  DEFENDER_ROTATE_KEY    path of the private key THIS host is authoritative
                         for (web=/root/api_svc_key,
                         api=/home/svc/.ssh/id_ed25519). Empty = no rotation.
  DEFENDER_ROTATE_PUB    filename under /keyshare the new public half is
                         published to (api_svc_key.pub | ops_id_ed25519.pub).
  DEFENDER_ROTATE_CHOWN  optional user:group the regenerated keypair is
                         chowned to (api uses svc:svc so the svc account can
                         still read its own db credential).
  ROTATE_S               rotation interval in seconds (default 600).

Every countermeasure is streamed as JSONL to /defenderlog/defender.jsonl
(bind-mounted to the host's transcripts/ for ALL sentinels — writes are
line-atomic so the three hosts can share the file). Schema:
  {"ts": float, "iso": "HH:MM:SS", "host": str, "type": str, "detail": str}"""

import json
import os
import re
import subprocess
import time

LOG = "/defenderlog/defender.jsonl"
SHELLS = {"sh", "bash", "dash", "ash", "nc", "ncat", "socat", "perl"}
KILL_AFTER_S = 20
LOCKDOWN_S = 300
ROTATE_S = int(os.environ.get("ROTATE_S", "600"))

HOST = os.environ.get("DEFENDER_HOST", "web")
KILL_USER = os.environ.get("DEFENDER_KILL_USER", "")
HONEYPOT_LOG = os.environ.get("DEFENDER_HONEYPOT_LOG", "")
ROTATE_KEY = os.environ.get("DEFENDER_ROTATE_KEY", "")
ROTATE_PUB = os.environ.get("DEFENDER_ROTATE_PUB", "")
ROTATE_CHOWN = os.environ.get("DEFENDER_ROTATE_CHOWN", "")


def event(kind, detail):
    rec = {"ts": time.time(), "iso": time.strftime("%H:%M:%S"),
           "host": HOST, "type": kind, "detail": detail}
    try:
        # open-append + a single write + flush: line-atomic across the
        # three containers sharing this bind-mounted file.
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
    except OSError:
        pass


def kill_shells():
    if not KILL_USER:
        return
    out = subprocess.run(
        ["ps", "-eo", "user,etimes,stat,comm,pid", "--no-headers"],
        capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        user, etimes, stat, comm, pid = parts
        if stat.startswith("Z"):  # zombie: already dead, just unreaped
            continue
        if user == KILL_USER and comm in SHELLS and int(etimes) > KILL_AFTER_S:
            subprocess.run(["kill", "-9", pid], capture_output=True)
            event("kill_shell",
                  f"killed {comm} (pid {pid}) owned by {KILL_USER} after {etimes}s")


def watch_log(state):
    try:
        with open(HONEYPOT_LOG, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return
    seen = state.setdefault("seen", 0)
    for line in lines[seen:]:
        m = re.search(r"HONEYPOT (\S+)", line)
        if m:
            ip = m.group(1)
            if ip not in state["locked"]:
                subprocess.run(["iptables", "-A", "INPUT", "-s", ip, "-j", "DROP"],
                               capture_output=True)
                state["locked"][ip] = time.time()
                event("lockdown", f"trap triggered — {ip} firewalled for {LOCKDOWN_S}s")
    state["seen"] = len(lines)
    for ip, t0 in list(state["locked"].items()):
        if time.time() - t0 > LOCKDOWN_S:
            subprocess.run(["iptables", "-D", "INPUT", "-s", ip, "-j", "DROP"],
                           capture_output=True)
            del state["locked"][ip]
            event("lockdown_lifted", f"{ip} readmitted to the network")


def rotate_key():
    """Regenerate the keypair this host owns and publish the new public
    half to the keyshare. Any copy of the old private key stops working on
    the downstream host (whose watcher syncs authorized_keys)."""
    priv, pub = ROTATE_KEY, ROTATE_KEY + ".pub"
    subprocess.run(["rm", "-f", priv, pub], capture_output=True)
    r = subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "",
                        "-f", priv, "-q"], capture_output=True)
    if r.returncode != 0:
        event("error", f"ssh-keygen failed for {priv}: {r.stderr.decode()}")
        return
    if ROTATE_CHOWN:
        subprocess.run(["chown", ROTATE_CHOWN, priv, pub], capture_output=True)
    subprocess.run(["chmod", "600", priv], capture_output=True)
    subprocess.run(["cp", pub, f"/keyshare/{ROTATE_PUB}"], capture_output=True)
    event("rotate_key", f"{ROTATE_PUB} keypair rotated — old keys revoked")


def main():
    os.makedirs("/defenderlog", exist_ok=True)
    duties = [f"kill_shells({KILL_USER})" if KILL_USER else None,
              "honeypot" if HONEYPOT_LOG else None,
              f"rotate({ROTATE_PUB}) every {ROTATE_S}s" if ROTATE_KEY else None]
    event("started", f"sentinel online — " + ", ".join(d for d in duties if d))
    state = {"locked": {}}
    last_rotate = time.time()
    while True:
        try:
            kill_shells()
            if HONEYPOT_LOG:
                watch_log(state)
            if ROTATE_KEY and time.time() - last_rotate > ROTATE_S:
                rotate_key()
                last_rotate = time.time()
        except Exception as exc:
            event("error", f"{type(exc).__name__}: {exc}")
        time.sleep(5)


if __name__ == "__main__":
    main()
