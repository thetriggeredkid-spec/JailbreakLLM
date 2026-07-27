"""Append-only JSONL transcript. Every event of the run — thinking blocks,
assistant text, tool calls, tool results, token usage, status changes — is
recorded here verbatim. docs/render.py turns this into the live HTML page."""

import json
import os
import time


class Transcript:
    def __init__(self, transcripts_dir: str):
        os.makedirs(transcripts_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        self.path = os.path.join(transcripts_dir, f"run-{ts}.jsonl")
        self._fh = open(self.path, "a", encoding="utf-8")

    def event(self, kind: str, **data):
        rec = {"ts": time.time(), "iso": time.strftime("%H:%M:%S"), "type": kind, **data}
        self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self):
        self._fh.close()
