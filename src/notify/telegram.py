import requests

try:
    from src.config.config_manager import load_config
except Exception:  # pragma: no cover
    load_config = None


def send_message(text, token=None, chat_id=None):
    """Send a Telegram message. Falls back to config if token/chat_id not provided.

    Returns True if sent successfully, else False.
    """
    try:
        if (not token or not chat_id) and load_config is not None:
            cfg = load_config()
            token = token or cfg.get("TELEGRAM_BOT_TOKEN", "")
            chat_id = chat_id or cfg.get("TELEGRAM_CHAT_ID", "")

        if not token or not chat_id:
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        resp = requests.post(url, json=payload, timeout=10)
        return 200 <= resp.status_code < 300
    except Exception:
        return False
