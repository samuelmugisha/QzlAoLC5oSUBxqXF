"""
Entry point for continuous operation.

Runs three jobs in one process:
  1. The trading cycle (src.main.run_once) on a fixed interval, read from
     config as DATA_FETCH_INTERVAL_MIN (default 30 minutes). This cycle
     sizes any new swing stop-loss from *intraday* ATR — see
     src/data/intraday_price_data.py and the main.py patch note.
  2. The daily regime job (src.jobs.daily_regime_job.run) once a day at
     00:05 UTC — recomputes the daily-candle forecast and LLM regime
     call, and writes the overrides file the trading cycle reads.
     This is the "slow" half of the Option C split: regime/forecast
     stays on daily candles, stop-loss sizing does not.
  3. The weekly Gmail report (src.notify.gmail_report.send_weekly_report),
     scheduled from config as REPORT_CRON (default "0 9 * * 1" — Monday 9am).

This is the piece the original notebook's "Next Steps" section pointed
to but didn't implement. Kept intentionally simple (one process, one
scheduler) per the brief's "System Simplicity" criterion — splitting
this into separate services is a reasonable next step but isn't needed
to satisfy "runs 24/7."

Run directly:  python scheduler.py
Or via Docker: see Dockerfile in this directory.
"""

import logging
import signal
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config.config_manager import load_config
from src.main import run_once
from src.jobs import daily_regime_job

try:
    from src.notify.gmail_report import send_weekly_report
except ImportError:
    send_weekly_report = None  # Gmail report module not wired up yet — job is skipped, not fatal.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scheduler")


def _safe_run_once():
    """Wrap run_once() so one bad cycle (exchange down, malformed data,
    a bug) logs and moves on instead of killing the whole scheduler."""
    try:
        logger.info("Starting trading cycle at %s", datetime.now().isoformat())
        run_once()
    except Exception:
        logger.exception("Trading cycle raised an exception — will retry next interval")


def _safe_daily_regime_job(cfg: dict):
    """Wrap the daily regime job so a failure (data fetch down, ARIMA
    fit error, LLM call failing) logs and leaves yesterday's overrides
    in place rather than crashing the scheduler or blanking the file."""
    try:
        logger.info("Starting daily regime job at %s", datetime.now().isoformat())
        daily_regime_job.run(cfg)
    except Exception:
        logger.exception("Daily regime job failed — trading cycle will keep using the last-written overrides")


def _safe_send_weekly_report():
    if send_weekly_report is None:
        logger.warning("Weekly report job fired but src.notify.gmail_report is not available — skipping")
        return
    try:
        logger.info("Sending weekly report at %s", datetime.now().isoformat())
        send_weekly_report()
    except Exception:
        logger.exception("Weekly report job failed")


def _parse_cron(cron_str: str) -> CronTrigger:
    """REPORT_CRON is stored as a standard 5-field cron string,
    e.g. "0 9 * * 1" = minute hour day month day_of_week (Monday=1)."""
    minute, hour, day, month, day_of_week = cron_str.split()
    return CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week)


def main():
    cfg = load_config()
    interval_min = int(cfg.get("DATA_FETCH_INTERVAL_MIN", 30))
    report_cron = cfg.get("REPORT_CRON", "0 9 * * 1")

    scheduler = BlockingScheduler(timezone="UTC")

    scheduler.add_job(
        _safe_run_once,
        trigger="interval",
        minutes=interval_min,
        id="trading_cycle",
        next_run_time=datetime.now(),  # run once immediately on startup
        max_instances=1,  # never let a slow cycle overlap the next one
        coalesce=True,
    )

    scheduler.add_job(
        _safe_daily_regime_job,
        args=[cfg],
        trigger=CronTrigger(hour=0, minute=5),
        id="daily_regime_job",
        max_instances=1,
        next_run_time=datetime.now(),  # also run once immediately so overrides exist on first boot
    )

    scheduler.add_job(
        _safe_send_weekly_report,
        trigger=_parse_cron(report_cron),
        id="weekly_report",
        max_instances=1,
    )

    def _shutdown(signum, frame):
        logger.info("Received signal %s — shutting down scheduler", signum)
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info(
        "Scheduler starting — trading cycle every %s min, daily regime job at 00:05 UTC, weekly report on cron '%s'",
        interval_min,
        report_cron,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
