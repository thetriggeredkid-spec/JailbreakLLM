"""Hard budget guardrails. The run halts and checkpoints the moment any cap
trips; resume continues from the checkpoint instead of re-spending."""

from dataclasses import dataclass, field


@dataclass
class Budget:
    max_turns: int
    max_total_tokens: int
    max_usd: float
    input_price: float   # USD per million tokens
    output_price: float  # USD per million tokens
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    history: list = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def usd_estimate(self) -> float:
        return (
            self.input_tokens * self.input_price / 1_000_000
            + self.output_tokens * self.output_price / 1_000_000
        )

    def record_turn(self, usage) -> None:
        self.turns += 1
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        self.input_tokens += in_tok
        self.output_tokens += out_tok
        self.history.append({"input": in_tok, "output": out_tok})

    def violation(self) -> str | None:
        if self.turns >= self.max_turns:
            return f"max_turns reached ({self.max_turns})"
        if self.total_tokens >= self.max_total_tokens:
            return f"max_total_tokens reached ({self.total_tokens}/{self.max_total_tokens})"
        if self.usd_estimate >= self.max_usd:
            return f"max_usd_estimate reached (${self.usd_estimate:.2f}/${self.max_usd:.2f})"
        return None

    def summary(self) -> str:
        return (
            f"turns={self.turns}/{self.max_turns} "
            f"tokens={self.total_tokens}/{self.max_total_tokens} "
            f"est=${self.usd_estimate:.3f}/${self.max_usd:.2f}"
        )
