"""
Threshold adaptation: maps a statistical forecast (and, optionally, an
LLM regime classification) to runtime overrides for the hybrid strategy.

The rule-based math from the original notebook is unchanged and remains
the source of truth for atr_k_stop / dca_drop_percent bounds — the LLM
can only nudge `enable_swing` and attach a rationale for the audit log.
It can never push a threshold outside the [1.0, 2.5] / [1.0, 8.0] clamps
below, so a bad or malicious LLM response degrades to "slightly more or
less swing-friendly," never to "no stop-loss" or "unbounded DCA size."
"""

import json
import logging
from pathlib import Path

from src.ml.llm_regime import classify_regime

logger = logging.getLogger("threshold_adapter")

RUNTIME_OVERRIDES = Path("data/runtime_overrides.json")


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def adapt_thresholds(base_cfg, pred_return, pred_strength, atr_value):
    """Map forecast to simple runtime overrides for hybrid mode.

    - Tighten ATR k slightly if positive expectation; loosen if negative.
    - Nudge DCA drop percent inversely to expectation.

    Unchanged from the original notebook — this is the deterministic
    core that the LLM layer in adapt_thresholds_with_llm() builds on top of.
    """
    base_k = float(base_cfg.get("ATR_MULTIPLIER", 1.5))
    base_drop = float(base_cfg.get("DCA_DROP_PERCENT", 3.0))

    k = base_k - pred_return * 5.0
    k = _clamp(k, 1.0, 2.5)

    drop = base_drop + (-pred_return * 100.0) * 0.3
    drop = _clamp(drop, 1.0, 8.0)

    enable_swing = pred_return > 0 and pred_strength > 0.2

    return {
        "enable_swing": bool(enable_swing),
        "atr_k_stop": round(k, 2),
        "dca_drop_percent": round(drop, 2),
    }


def adapt_thresholds_with_llm(base_cfg, pred_return, pred_strength, atr_value, current_price, feature_snapshot=None):
    """
    Same as adapt_thresholds(), plus an optional LLM regime call that can
    only adjust `enable_swing` (never the numeric thresholds) and always
    attaches a human-readable rationale for the log/report.

    `feature_snapshot` is an optional dict of additional indicators
    (rsi, macd_hist, volume_z, price_vs_sma200_pct, ...) to give the LLM
    more context than pred_return/pred_strength alone. If omitted, only
    the forecast + ATR are sent.
    """
    base_overrides = adapt_thresholds(base_cfg, pred_return, pred_strength, atr_value)

    provider = (base_cfg.get("LLM_PROVIDER") or "").strip().lower()
    if provider not in ("anthropic", "openai"):
        # No LLM configured — behave exactly like the original function.
        return {**base_overrides, "llm_regime": None, "llm_rationale": None}

    atr_pct_of_price = (float(atr_value) / float(current_price)) if current_price else 0.0
    features = {
        "pred_return": round(float(pred_return), 5),
        "pred_strength": round(float(pred_strength), 3),
        "atr_pct_of_price": round(atr_pct_of_price, 5),
    }
    if feature_snapshot:
        features.update(feature_snapshot)

    recommendation = classify_regime(features, base_cfg)

    # The LLM may only *tighten* eligibility for swing trades — it can
    # suggest disabling a swing entry the rule-based logic allowed, or
    # simply agree/disagree, but it can never force a swing trade the
    # deterministic ATR/return logic didn't already permit.
    final_enable_swing = base_overrides["enable_swing"] and (
        recommendation.source == "fallback" or recommendation.swing_entry_candidate
    )

    result = {
        **base_overrides,
        "enable_swing": final_enable_swing,
        "llm_regime": recommendation.regime,
        "llm_confidence": recommendation.confidence,
        "llm_rationale": recommendation.rationale,
    }
    logger.info("Threshold adaptation: %s", result)
    return result


def write_overrides(payload):
    RUNTIME_OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
    with RUNTIME_OVERRIDES.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def read_overrides():
    if not RUNTIME_OVERRIDES.exists():
        return {}
    try:
        with RUNTIME_OVERRIDES.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
