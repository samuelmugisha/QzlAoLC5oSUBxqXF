"""
LLM-assisted regime classification.

Design intent (see project blueprint discussion):
  - The LLM never sees raw candles and never places a trade.
  - It receives a small, already-computed feature snapshot and returns a
    small, strictly-typed JSON recommendation.
  - Its output only ever *nudges* the existing rule-based adapter in
    threshold_adapter.py — it cannot set atr_k_stop or dca_drop_percent
    outside the bounds the rule-based logic already enforces.
  - Every call is logged with its rationale so a reviewer (or you, at
    3am when something looks wrong) can see *why* the mode changed.
  - If the call fails, times out, or returns something that doesn't
    validate, we fall back to the rule-based result with no LLM input.
    A 24/7 agent cannot stall because an API call hung.

Supports two providers via LLM_PROVIDER config: "anthropic" or "openai".
Both are optional — if LLM_PROVIDER is unset or the API call fails,
callers get a neutral/no-op recommendation and the system runs exactly
as it did before this module existed.
"""

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger("llm_regime")

# Where we log every LLM call (request + response + rationale) for audit.
LLM_LOG_PATH = Path("data/llm_regime_log.jsonl")

VALID_REGIMES = {"trending_up", "trending_down", "ranging", "volatile_breakout"}


@dataclass
class RegimeRecommendation:
    """Strictly-typed result. This is the ONLY thing the LLM is allowed
    to influence — it is a recommendation, not a command."""
    regime: str = "ranging"
    confidence: float = 0.0
    flagged_features: Optional[list] = None
    swing_entry_candidate: bool = False
    rationale: str = "LLM unavailable — using neutral default"
    source: str = "fallback"  # "llm" or "fallback"

    def __post_init__(self):
        if self.flagged_features is None:
            self.flagged_features = []


def _build_prompt(features: dict) -> str:
    """Keep the prompt small and numeric — no raw candles, no free text
    market commentary to parse. This keeps cost and latency low and
    keeps the model's job narrow (classification, not analysis)."""
    return (
        "You are a narrow market-regime classifier for a Bitcoin trading system. "
        "Given these pre-computed indicator values, classify the current regime.\n\n"
        f"Features: {json.dumps(features)}\n\n"
        "Respond with ONLY a JSON object, no prose, matching exactly this schema:\n"
        '{"regime": one of ["trending_up","trending_down","ranging","volatile_breakout"], '
        '"confidence": float 0-1, '
        '"flagged_features": list of up to 3 feature names most relevant to this call, '
        '"swing_entry_candidate": boolean, '
        '"rationale": one short sentence (<25 words) explaining the classification}'
    )


def _validate(payload: dict) -> Optional[RegimeRecommendation]:
    """Reject anything that doesn't match the schema exactly. Never trust
    an LLM response structurally — validate before it touches strategy logic."""
    try:
        regime = payload.get("regime")
        if regime not in VALID_REGIMES:
            return None
        confidence = float(payload.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))
        flagged = payload.get("flagged_features", [])
        if not isinstance(flagged, list):
            flagged = []
        flagged = [str(f) for f in flagged][:3]
        swing_candidate = bool(payload.get("swing_entry_candidate", False))
        rationale = str(payload.get("rationale", ""))[:200]
        return RegimeRecommendation(
            regime=regime,
            confidence=confidence,
            flagged_features=flagged,
            swing_entry_candidate=swing_candidate,
            rationale=rationale or "No rationale provided",
            source="llm",
        )
    except Exception:
        return None


def _call_anthropic(prompt: str, api_key: str, model: str, timeout: int) -> Optional[dict]:
    import requests

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model or "claude-haiku-4-5-20251001",
            "max_tokens": 200,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
    return json.loads(text.strip().strip("`").removeprefix("json").strip())


def _call_openai(prompt: str, api_key: str, model: str, timeout: int) -> Optional[dict]:
    import requests

    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model or "gpt-4o-mini",
            "max_tokens": 200,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["choices"][0]["message"]["content"]
    return json.loads(text.strip())


def classify_regime(features: dict, cfg: dict, timeout: int = 10) -> RegimeRecommendation:
    """
    Main entry point. `features` should be the small numeric snapshot
    (rsi, macd_hist, atr_pct_of_price, volume_z, price_vs_sma200_pct, ...) —
    not raw candles. `cfg` is the dict returned by load_config().

    Always returns a RegimeRecommendation — never raises. Callers should
    treat a `source == "fallback"` result as "no LLM input this cycle",
    not as an error condition worth alerting on.
    """
    provider = (cfg.get("LLM_PROVIDER") or "").strip().lower()
    fallback = RegimeRecommendation()

    if provider not in ("anthropic", "openai"):
        return fallback

    prompt = _build_prompt(features)
    raw_response = None
    try:
        if provider == "anthropic":
            api_key = cfg.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                return fallback
            raw_response = _call_anthropic(prompt, api_key, cfg.get("LLM_MODEL", ""), timeout)
        elif provider == "openai":
            api_key = cfg.get("OPENAI_API_KEY", "")
            if not api_key:
                return fallback
            raw_response = _call_openai(prompt, api_key, cfg.get("LLM_MODEL", ""), timeout)
    except Exception as e:
        logger.warning("LLM regime call failed, using fallback: %s", e)
        _log_call(features, None, fallback, error=str(e))
        return fallback

    result = _validate(raw_response) if raw_response else None
    if result is None:
        logger.warning("LLM regime response failed validation, using fallback: %r", raw_response)
        _log_call(features, raw_response, fallback, error="validation_failed")
        return fallback

    _log_call(features, raw_response, result, error=None)
    return result


def _log_call(features: dict, raw_response: Optional[dict], result: RegimeRecommendation, error: Optional[str]):
    """Append-only audit log — every LLM call, success or failure, with
    enough detail to reconstruct why a mode switch happened."""
    try:
        LLM_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": __import__("datetime").datetime.now().isoformat(),
            "features": features,
            "raw_response": raw_response,
            "result": asdict(result),
            "error": error,
        }
        with LLM_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        # Logging must never break the trading loop.
        pass
