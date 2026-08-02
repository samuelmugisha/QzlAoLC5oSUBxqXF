"""
Configuration loading.

Fix applied vs. the original notebook version: the Google Sheet override
merge previously did `for k, v in overrides.items(): if k in cfg: cfg[k] = v`
against the FULL config dict — which meant a stray row in the Sheet named
e.g. "COINBASE_API_KEY" would silently overwrite the real secret loaded
from .env. Secrets and sheet-overridable strategy params are now two
separate dicts, and only the strategy dict is ever touched by sheet data.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

CACHE_PATH = Path("config/settings_cache.json")

# Only these keys may ever be set by the Google Sheet. Anything else in
# the Sheet is ignored — this is the allowlist that closes the security
# gap described above.
SHEET_OVERRIDABLE_KEYS = {
    "BUDGET_USD",
    "DCA_AMOUNT_USD",
    "DCA_DROP_PERCENT",
    "ATR_MULTIPLIER",
    "DATA_FETCH_INTERVAL_MIN",
    "REPORT_CRON",
    "MAX_DRAWDOWN_PCT",
    "TRADING_MODE",
    "SWING_AMOUNT_USD",
    "DCA_MIN_INTERVAL_HOURS",
    "enable_swing",
    "atr_k_stop",
    "dca_drop_percent",
}


def _cast(value: str):
    text = (value or "").strip()
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        return int(text) if "." not in text else float(text)
    except ValueError:
        return text


def _read_sheet_overrides(sheet_id: str, tab: str, sa_json_path: str) -> dict:
    try:
        from google.oauth2.service_account import Credentials
        import gspread

        creds = Credentials.from_service_account_file(sa_json_path, scopes=SCOPES)
        ws = gspread.authorize(creds).open_by_key(sheet_id).worksheet(tab)
        rows = ws.get_all_values()
        overrides: dict = {}
        for row in rows:
            if not row:
                continue
            key = (row[0] or "").strip()
            if not key or key.startswith("#"):
                continue
            # Allowlist enforced here — anything not in
            # SHEET_OVERRIDABLE_KEYS is silently dropped rather than
            # merged into config, no matter what the Sheet contains.
            if key not in SHEET_OVERRIDABLE_KEYS:
                continue
            val = (row[1] if len(row) > 1 else "").strip()
            overrides[key] = _cast(val)
        return overrides
    except Exception:
        return {}


def load_config() -> dict:
    """Load .env (secrets + defaults), then apply allowlisted overrides
    from the Google Sheet (falling back to the local cache if the Sheet
    is unreachable)."""
    load_dotenv(".env")

    secrets = {
        # Coinbase
        "COINBASE_API_KEY": os.getenv("COINBASE_API_KEY", ""),
        "COINBASE_API_SECRET": os.getenv("COINBASE_API_SECRET", ""),
        "COINBASE_BASE_URL": os.getenv("COINBASE_BASE_URL", "https://api.coinbase.com"),
        "COINBASE_API_PASSPHRASE": os.getenv("COINBASE_API_PASSPHRASE", ""),
        # Google Sheets
        "GOOGLE_SHEET_ID": os.getenv("GOOGLE_SHEET_ID", ""),
        "GOOGLE_SHEET_TAB": os.getenv("GOOGLE_SHEET_TAB", "Config"),
        "GOOGLE_SERVICE_ACCOUNT_JSON_PATH": os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_PATH", ""),
        # Telegram
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", ""),
        # Gmail
        "GMAIL_USER": os.getenv("GMAIL_USER", ""),
        "GMAIL_APP_PASSWORD": os.getenv("GMAIL_APP_PASSWORD", ""),
        # LLM
        "LLM_PROVIDER": os.getenv("LLM_PROVIDER", ""),
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
        "LLM_MODEL": os.getenv("LLM_MODEL", ""),
    }

    strategy = {
        "ENV": os.getenv("ENV", "dev"),
        "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO"),
        "BUDGET_USD": _cast(os.getenv("BUDGET_USD", "10000")),
        "DCA_AMOUNT_USD": _cast(os.getenv("DCA_AMOUNT_USD", "500")),
        "DCA_DROP_PERCENT": _cast(os.getenv("DCA_DROP_PERCENT", "3.0")),
        "DCA_MIN_INTERVAL_HOURS": _cast(os.getenv("DCA_MIN_INTERVAL_HOURS", "24")),
        "ATR_MULTIPLIER": _cast(os.getenv("ATR_MULTIPLIER", "1.5")),
        "DATA_FETCH_INTERVAL_MIN": _cast(os.getenv("DATA_FETCH_INTERVAL_MIN", "30")),
        "REPORT_CRON": os.getenv("REPORT_CRON", "0 9 * * 1"),
        "MAX_DRAWDOWN_PCT": _cast(os.getenv("MAX_DRAWDOWN_PCT", "25.0")),
        "TRADING_MODE": os.getenv("TRADING_MODE", "hybrid"),
        "SWING_AMOUNT_USD": _cast(os.getenv("SWING_AMOUNT_USD", "500")),
    }

    # Best-effort remote overrides -> save cache; else try cache.
    sheet_id = secrets["GOOGLE_SHEET_ID"]
    sa_json = secrets["GOOGLE_SERVICE_ACCOUNT_JSON_PATH"]
    tab = secrets["GOOGLE_SHEET_TAB"]

    overrides = {}
    if sheet_id and sa_json and Path(sa_json).exists():
        overrides = _read_sheet_overrides(sheet_id, tab, sa_json)
        if overrides:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with CACHE_PATH.open("w", encoding="utf-8") as f:
                json.dump(overrides, f, indent=2)

    if not overrides and CACHE_PATH.exists():
        try:
            with CACHE_PATH.open("r", encoding="utf-8") as f:
                overrides = json.load(f)
        except Exception:
            overrides = {}

    for k, v in overrides.items():
        if k in strategy:  # allowlist already enforced in _read_sheet_overrides;
            strategy[k] = v  # this second check guards the cached-file path too.

    return {**secrets, **strategy}
