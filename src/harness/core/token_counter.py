"""TokenCounter — accumulates and formats token usage across LLM calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.llm.types import LlmUsage

# Approximate pricing per 1M tokens (USD)
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # (input_per_1M, output_per_1M)
    "claude-sonnet": (3.0, 15.0),
    "claude-opus": (15.0, 75.0),
    "claude-haiku": (0.80, 4.0),
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    "deepseek": (0.27, 1.10),
    "default": (1.0, 5.0),
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Rough cost estimate based on model pricing."""
    key = model.lower()
    price_in, price_out = _MODEL_PRICING.get("default", (1.0, 5.0))
    for prefix, prices in _MODEL_PRICING.items():
        if key.startswith(prefix):
            price_in, price_out = prices
            break
    return (input_tokens / 1_000_000) * price_in + (output_tokens / 1_000_000) * price_out


def _fmt_tokens(n: int) -> str:
    """Format token count: 1234 → '1.2k', 12345 → '12.3k'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _fmt_cost(c: float) -> str:
    """Format cost estimate."""
    if c < 0.01:
        return f"${c:.4f}"
    if c < 1.0:
        return f"${c:.3f}"
    return f"${c:.2f}"


@dataclass
class TokenCounter:
    """Accumulates token usage across LLM calls with cost estimation."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    call_count: int = 0
    _model_totals: dict[str, tuple[int, int]] = field(default_factory=dict)

    def add(self, usage: "LlmUsage", model: str = "") -> None:
        """Add one LLM call's usage to the counter."""
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0)
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0)
        self.call_count += 1
        if model:
            prev = self._model_totals.get(model, (0, 0))
            self._model_totals[model] = (prev[0] + usage.input_tokens, prev[1] + usage.output_tokens)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_est(self) -> float:
        """Recompute cost from per-model totals."""
        total = 0.0
        for model, (inp, out) in self._model_totals.items():
            total += _estimate_cost(model, inp, out)
        return total

    def format_last_call(self, usage: "LlmUsage") -> str:
        """Format the most recent call: '📊 8.2k in / 1.5k out'."""
        inp = _fmt_tokens(usage.input_tokens)
        out = _fmt_tokens(usage.output_tokens)
        cache = ""
        cr = getattr(usage, "cache_read_input_tokens", 0)
        if cr > 0:
            cache = f" · {_fmt_tokens(cr)} cached"
        return f"📊 {inp} in / {out} out{cache}"

    def format_summary(self) -> str:
        """Format cumulative summary: '📊 45k tokens (32k in / 13k out) · 8 calls'."""
        total = _fmt_tokens(self.total)
        inp = _fmt_tokens(self.input_tokens)
        out = _fmt_tokens(self.output_tokens)
        cost = _fmt_cost(self.cost_est)
        parts = [f"📊 {total} tokens ({inp} in / {out} out) · {self.call_count} calls · ~{cost}"]
        if self.cache_read_tokens > 0:
            parts.append(f"💾 {_fmt_tokens(self.cache_read_tokens)} cached")
        return " · ".join(parts)
