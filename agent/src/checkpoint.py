"""Checkpoint/restore of the full conversation state (message history,
budget counters, captured flags) so a halted run can resume without
re-spending tokens on work already done.

Message content blocks are serialized via model_dump() (plain JSON); the
Anthropic API accepts plain dicts for content blocks, including thinking
blocks with their signatures.
"""

import os
import pickle

CHECKPOINT_FILE = "session.pkl"


def _serialize_messages(messages):
    out = []
    for msg in messages:
        msg = dict(msg)  # preserve role, tool_calls, tool_call_id, etc.
        content = msg.get("content")
        if isinstance(content, list):
            msg["content"] = [
                block.model_dump() if hasattr(block, "model_dump") else block
                for block in content
            ]
        out.append(msg)
    return out


def save(checkpoints_dir: str, state: dict):
    os.makedirs(checkpoints_dir, exist_ok=True)
    state = dict(state)
    state["messages"] = _serialize_messages(state["messages"])
    path = os.path.join(checkpoints_dir, CHECKPOINT_FILE)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        pickle.dump(state, fh)
    os.replace(tmp, path)


def load(checkpoints_dir: str) -> dict | None:
    path = os.path.join(checkpoints_dir, CHECKPOINT_FILE)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return pickle.load(fh)


def clear(checkpoints_dir: str):
    path = os.path.join(checkpoints_dir, CHECKPOINT_FILE)
    if os.path.exists(path):
        os.remove(path)
