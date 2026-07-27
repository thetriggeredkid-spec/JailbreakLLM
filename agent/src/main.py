#!/usr/bin/env python3
"""Entry point: run / resume / campaign / status."""

import argparse
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)  # sibling modules: agent, tools, budget, transcript, checkpoint
PROJECT_ROOT = os.path.dirname(os.path.dirname(ROOT))

import agent
import campaign
import checkpoint
import mutator
from transcript import Transcript


def load_config():
    with open(os.path.join(PROJECT_ROOT, "agent", "config.yaml"), encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    # Resolve paths relative to the project root regardless of cwd.
    for key in ("transcripts_dir", "checkpoints_dir"):
        cfg["paths"][key] = os.path.join(PROJECT_ROOT, cfg["paths"][key])
    return cfg


def load_env():
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def main():
    parser = argparse.ArgumentParser(description="ExploitGym replica — autonomous agent harness")
    parser.add_argument("command", choices=["run", "resume", "campaign", "status"])
    args = parser.parse_args()

    load_env()
    config = load_config()
    checkpoints_dir = config["paths"]["checkpoints_dir"]

    if args.command == "status":
        state = checkpoint.load(checkpoints_dir)
        camp = campaign.load_state(config)
        if state:
            print(f"checkpoint: {state['budget'].summary()}, "
                  f"flags remaining: {len(state['remaining_flags'])}, "
                  f"messages: {len(state['messages'])}")
        else:
            print("no checkpoint — no interrupted run to resume")
        if camp["runs"]:
            escaped = sum(1 for r in camp["runs"] if r["status"] == "escaped")
            print(f"campaign: {len(camp['runs'])} runs, {escaped} escapes, "
                  f"est=${camp['total_usd']:.2f}")
        return 0

    if config.get("provider", "anthropic") == "openai_compat":
        key_env = config.get("openai_compat", {}).get("api_key_env", "OPENAI_API_KEY")
    else:
        key_env = "ANTHROPIC_API_KEY"
    if not os.environ.get(key_env):
        print(f"error: {key_env} not set (copy .env.example to .env)", file=sys.stderr)
        return 1

    if args.command == "campaign":
        return campaign.run_campaign(config)

    if args.command == "run" and checkpoint.load(checkpoints_dir):
        print("note: a checkpoint exists; use 'resume' to continue it, or delete "
              "agent/checkpoints/session.pkl to start over", file=sys.stderr)

    # Per-run enclave mutation. `run` re-randomizes the gym when enabled;
    # `resume` continues against the enclave the checkpoint was taken in
    # (checkpoints pickle remaining_flags, so no re-mutation here).
    manifest = None
    if args.command == "run" and config.get("mutation", {}).get("enabled"):
        manifest = mutator.mutate(config)
        print(f"enclave mutated — seed={manifest['seed']} bridge={manifest['bridge']} "
              f"web vulns={','.join(manifest['vulns']['web'])} "
              f"suid={manifest['vulns']['suid']} db_sudo={manifest['vulns']['db_sudo']} "
              f"ports={manifest['ports']}", file=sys.stderr)
    elif args.command in ("run", "resume"):
        manifest = mutator.load_manifest(checkpoints_dir)

    transcript = Transcript(config["paths"]["transcripts_dir"])
    print(f"transcript: {transcript.path}", file=sys.stderr)
    try:
        result = agent.run_experiment(
            config, transcript, resume=(args.command == "resume"),
            password=config["foothold_password"], manifest=manifest,
        )
    finally:
        transcript.close()
    print(f"result: {result}", file=sys.stderr)

    # Persistent memory updates after ANY completed attempt (not just
    # campaign mode) — this is what populates the dashboard's memory panel.
    if result["status"] != "interrupted":
        try:
            memory = campaign._read_memory(config)
            memory = campaign.update_memory(config, memory, result, transcript.path)
            campaign._write_memory(config, memory)
            print("persistent memory updated", file=sys.stderr)
        except Exception as exc:
            print(f"memory update failed ({exc}); keeping previous memory",
                  file=sys.stderr)

    if result["status"] == "escaped":
        return 0
    if result["status"] == "interrupted":
        return 130
    return 2


if __name__ == "__main__":
    sys.exit(main())
