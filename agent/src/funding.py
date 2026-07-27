"""Funding watcher: the autonomous money layer. Before each campaign run it
checks whether there is enough API budget to proceed. Two balance sources:

- "openrouter": live balance from the OpenRouter credits API (requires an
  OpenRouter management key in env). This is the rail that supports Claude
  with autonomous top-up semantics: pause when low, resume when topped up.
- "local": works with the direct Anthropic API today. Anthropic has no
  balance endpoint, so the "balance" is a manually configured float minus
  the campaign's tracked spend. It still gives pause/resume behavior — the
  campaign waits when the float is exhausted and resumes after you refill it
  (i.e., bump local_float_usd).

The watcher never touches wallets or makes payments; it only observes and
gates. That keeps every key-handling risk out of the agent's reach."""

import json
import os
import time
import urllib.request

OPENROUTER_CREDITS_URL = "https://openrouter.ai/api/v1/credits"


def parse_openrouter_credits(payload: dict) -> float:
    data = payload.get("data", payload)
    return float(data["total_credits"]) - float(data["total_usage"])


class FundingWatcher:
    def __init__(self, config: dict):
        f = config.get("funding", {})
        self.provider = f.get("provider", "local")
        self.local_float = float(f.get("local_float_usd", 0.0))
        self.threshold = float(f.get("low_balance_usd", 5.0))
        self.wait = bool(f.get("wait_for_topup", True))
        self.poll_s = int(f.get("poll_interval_s", 300))
        self._state_path = os.path.join(
            config["paths"]["transcripts_dir"], "campaign.json")

    def balance_usd(self) -> float | None:
        """Current estimated API budget in USD, or None if unknown."""
        if self.provider == "openrouter":
            key = os.environ.get("OPENROUTER_MANAGEMENT_KEY", "")
            if not key:
                return None
            req = urllib.request.Request(
                OPENROUTER_CREDITS_URL,
                headers={"Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return parse_openrouter_credits(json.load(resp))
        # local: configured float minus campaign-tracked spend
        spent = 0.0
        if os.path.exists(self._state_path):
            with open(self._state_path, encoding="utf-8") as fh:
                spent = float(json.load(fh).get("total_usd", 0.0))
        return self.local_float - spent

    def ensure_funds(self) -> float | None:
        """Block until the balance is above the low-water mark.

        Returns the balance when proceeding, None when it is unknown (in
        which case we proceed anyway — budget caps remain the backstop).
        Raises SystemExit if funds are low and wait_for_topup is off."""
        notified = False
        while True:
            balance = self.balance_usd()
            if balance is None:
                if not notified:
                    print("[funding] balance unknown (no provider key); "
                          "proceeding on budget caps alone", flush=True)
                return None
            if balance >= self.threshold:
                if notified:
                    print(f"[funding] top-up detected (${balance:.2f}); resuming",
                          flush=True)
                return balance
            if not self.wait:
                raise SystemExit(
                    f"[funding] balance ${balance:.2f} below threshold "
                    f"${self.threshold:.2f} and wait_for_topup is off — halting")
            if not notified:
                print(f"[funding] balance ${balance:.2f} below threshold "
                      f"${self.threshold:.2f} — paused, polling every "
                      f"{self.poll_s}s for a top-up", flush=True)
                notified = True
            time.sleep(self.poll_s)
