"""Provider backends. The agent loop talks to a normalized Turn/blocks
interface; each backend owns its provider-native message history and API
details. Two providers:

- "anthropic": Claude via the anthropic SDK, extended/interleaved thinking.
- "openai_compat": any OpenAI-chat-completions-compatible endpoint (Kimi /
  Moonshot, DeepSeek, OpenAI, OpenRouter...). Reasoning content returned by
  thinking models (kimi-k*-thinking, deepseek-reasoner) is logged as
  "thinking" for the dashboard. Uses raw HTTP — no extra dependencies.
"""

import json
import os
import urllib.request
from types import SimpleNamespace

from tools import TOOL_SCHEMAS

# Convert our tool schemas once for OpenAI-style endpoints.
OPENAI_TOOLS = [
    {"type": "function", "function": {
        "name": t["name"],
        "description": t["description"],
        "parameters": t["input_schema"],
    }}
    for t in TOOL_SCHEMAS
]


def _usage(input_tokens, output_tokens):
    return SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)


class Turn:
    def __init__(self, blocks, tool_calls, usage, stop_reason):
        self.blocks = blocks          # [{"type": "thinking"|"text"|"tool_call", ...}]
        self.tool_calls = tool_calls  # [{"id":..., "name":..., "input": {...}}]
        self.usage = usage
        self.stop_reason = stop_reason


# ── Anthropic ───────────────────────────────────────────────────────────────

class AnthropicBackend:
    def __init__(self, config, system_prompt, api_key_env="ANTHROPIC_API_KEY"):
        from anthropic import Anthropic
        self.client = Anthropic()
        self.config = config
        self.system_prompt = system_prompt
        self.messages = []

    def export_messages(self):
        out = []
        for msg in self.messages:
            content = msg["content"]
            if isinstance(content, list):
                content = [
                    b.model_dump() if hasattr(b, "model_dump") else b
                    for b in content
                ]
            out.append({"role": msg["role"], "content": content})
        return out

    def import_messages(self, messages):
        self.messages = list(messages)

    def step(self) -> Turn:
        think_cfg = self.config.get("thinking", {})
        kwargs = dict(
            model=self.config["model"],
            max_tokens=self.config["max_tokens"],
            system=self.system_prompt,
            tools=TOOL_SCHEMAS,
            messages=self.messages,
        )
        if think_cfg.get("type") == "adaptive":
            # 5-series models: adaptive thinking steered by effort
            kwargs["extra_body"] = {
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": think_cfg.get("effort", "medium")},
            }
        else:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": int(think_cfg.get("budget_tokens", 10000)),
            }
            # Without this beta, thinking blocks only appear in the first
            # assistant turn; with it, every turn narrates.
            kwargs["extra_headers"] = {
                "anthropic-beta": "interleaved-thinking-2025-05-14",
            }
        r = self.client.messages.create(**kwargs)
        self.messages.append({"role": "assistant", "content": r.content})

        blocks, tool_calls = [], []
        for b in r.content:
            if b.type == "thinking":
                blocks.append({"type": "thinking", "text": b.thinking})
            elif b.type == "text":
                blocks.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                blocks.append({"type": "tool_call", "name": b.name, "input": b.input, "id": b.id})
                tool_calls.append({"id": b.id, "name": b.name, "input": b.input})
        return Turn(blocks, tool_calls, _usage(r.usage.input_tokens, r.usage.output_tokens),
                    r.stop_reason)

    def add_tool_results(self, results):
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r["id"], "content": r["output"]}
            for r in results
        ]})

    def add_user(self, text):
        self.messages.append({"role": "user", "content": text})

    def summarize(self, instruction):
        """One-shot summarization of the conversation so far (for context
        compaction). Returns (text, usage)."""
        r = self.client.messages.create(
            model=self.config["model"],
            max_tokens=4000,
            messages=self.messages + [{"role": "user", "content": instruction}],
        )
        text = "".join(b.text for b in r.content if b.type == "text")
        return text, _usage(r.usage.input_tokens, r.usage.output_tokens)

    def restart(self, handoff_text):
        """Drop the conversation and continue from a compacted handoff."""
        self.messages = [{"role": "user", "content": handoff_text}]


# ── OpenAI-compatible ───────────────────────────────────────────────────────

class OpenAICompatBackend:
    def __init__(self, config, system_prompt, api_key_env=None):
        oc = config.get("openai_compat", {})
        self.base_url = oc.get("base_url", "https://api.openai.com/v1").rstrip("/")
        self.api_key = os.environ.get(api_key_env or oc.get("api_key_env", "OPENAI_API_KEY"), "")
        self.reasoning = bool(oc.get("reasoning", True))
        self.model = config["model"]
        self.max_tokens = config["max_tokens"]
        # some providers reject an empty system message — omit it
        self.messages = ([{"role": "system", "content": system_prompt}]
                         if system_prompt else [])

    def export_messages(self):
        return list(self.messages)

    def import_messages(self, messages):
        self.messages = list(messages)

    def step(self) -> Turn:
        payload = {
            "model": self.model,
            "messages": self.messages,
            "tools": OPENAI_TOOLS,
            "max_tokens": self.max_tokens,
        }
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.load(resp)

        choice = data["choices"][0]
        msg = choice["message"]
        finish = choice.get("finish_reason", "stop")

        # Assistant message is appended in provider-native form; reasoning
        # content is logged but NOT sent back (providers drop it anyway).
        self.messages.append({k: v for k, v in msg.items()
                              if k in ("role", "content", "tool_calls") and v is not None})

        blocks, tool_calls = [], []
        if self.reasoning and msg.get("reasoning_content"):
            blocks.append({"type": "thinking", "text": msg["reasoning_content"]})
        if msg.get("content"):
            blocks.append({"type": "text", "text": msg["content"]})
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {"command": tc["function"]["arguments"]}
            blocks.append({"type": "tool_call", "name": tc["function"]["name"],
                           "input": args, "id": tc["id"]})
            tool_calls.append({"id": tc["id"], "name": tc["function"]["name"], "input": args})

        stop_reason = {"stop": "end_turn", "tool_calls": "tool_use",
                       "length": "max_tokens", "content_filter": "refusal"}.get(finish, finish)
        if msg.get("refusal"):
            stop_reason = "refusal"
        usage = data.get("usage", {})
        return Turn(blocks, tool_calls,
                    _usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)),
                    stop_reason)

    def add_tool_results(self, results):
        for r in results:
            self.messages.append({"role": "tool", "tool_call_id": r["id"],
                                  "content": r["output"]})

    def add_user(self, text):
        self.messages.append({"role": "user", "content": text})

    def summarize(self, instruction):
        """One-shot summarization of the conversation so far (for context
        compaction). Returns (text, usage)."""
        payload = {
            "model": self.model,
            "messages": self.messages + [{"role": "user", "content": instruction}],
            "max_tokens": 4000,
        }
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.load(resp)
        usage = data.get("usage", {})
        return (data["choices"][0]["message"].get("content") or "",
                _usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)))

    def restart(self, handoff_text):
        """Drop the conversation and continue from a compacted handoff."""
        self.messages = [m for m in self.messages if m.get("role") == "system"]
        self.messages.append({"role": "user", "content": handoff_text})


def make_backend(config, system_prompt):
    provider = config.get("provider", "anthropic")
    if provider == "openai_compat":
        return OpenAICompatBackend(config, system_prompt)
    return AnthropicBackend(config, system_prompt)
