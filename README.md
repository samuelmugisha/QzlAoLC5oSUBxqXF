# Bitcoin Trading Agent

An autonomous, 24/7 cryptocurrency trading system that combines a rule-based
hybrid strategy (Dollar-Cost Averaging + ATR-based stop-losses) with a
statistical forecasting layer and a narrowly-scoped LLM regime classifier —
built, backtested, and containerized for continuous cloud deployment.

**Status**: paper-trading / simulation only. No real capital is at risk in
this repository's current configuration.

---

## Why this project

Most "AI trading bot" projects either (a) hand-wave the risk management or
(b) wire an LLM directly to order placement, which is a good way to lose
money in a way that's hard to explain afterward. This project was built to
do neither: every component that touches money is deterministic and
unit-testable, and the LLM's role is deliberately narrow — it classifies
market regime from a handful of pre-computed indicators and can only ever
*tighten* one eligibility flag, never move a dollar amount or override a
stop-loss.

## Key features

- **Hybrid strategy**: DCA as the always-on base layer, ATR-sized stop-losses
  for opportunistic swing entries, and a portfolio-level drawdown circuit
  breaker that pauses all new activity if losses exceed a configurable
  threshold.
- **Two-speed architecture**: a 30-minute trading cycle for price monitoring
  and execution, decoupled from a once-daily regime/forecast recompute —
  each ATR calculation uses the candle granularity appropriate to the
  question it's answering, rather than forcing one timeframe to serve both.
- **LLM-assisted regime classification**: an optional call (Anthropic or
  OpenAI) that turns pre-computed indicators into a regime label and a
  logged rationale. Every response is schema-validated; a failed or
  malformed call degrades to a safe rule-based default rather than
  breaking the trading loop.
- **Statistical forecasting**: ARIMA-based next-period return prediction,
  selected via walk-forward cross-validation against ES/AR/ARIMA
  candidates.
- **Config/secrets separation**: strategy parameters are hot-reloadable
  from a Google Sheet (hourly, with local JSON fallback) via an explicit
  allowlist; credentials live only in environment variables and are never
  reachable from the Sheet.
- **Full observability**: Telegram alerts on every trade and risk event,
  a weekly Gmail summary, and an append-only audit log of every LLM call.
- **Backtested against the same code path it trades with**: the backtest
  engine calls the identical `StrategyManager.evaluate_hybrid()` used
  live, so validation results reflect the actual trading logic rather than
  a parallel reimplementation.
- **Containerized**: Docker + docker-compose, with a persistent volume so
  trade history and the drawdown high-water mark survive restarts.

## Architecture

```
                    ┌─────────────────────────┐
                    │   Daily regime job       │   once/day, 00:05 UTC
                    │   (daily candles, ARIMA, │
                    │    optional LLM call)    │
                    └───────────┬─────────────┘
                                │ writes
                                ▼
                    runtime_overrides.json
                                │ reads
                                ▼
┌──────────────┐    ┌─────────────────────────┐    ┌──────────────────┐
│ Intraday data │──▶│  Trading cycle (30 min)  │──▶│  Paper broker /   │
│ (30-min ATR)  │   │  DCA + swing + risk check│   │  trade log        │
└──────────────┘    └───────────┬─────────────┘    └──────────────────┘
                                │
                    ┌───────────┴─────────────┐
                    ▼                          ▼
              Telegram alerts           Weekly Gmail report
```

Two independent clocks, one shared state directory, three layered safety
mechanisms (per-trade stop-loss, portfolio drawdown breaker, schema-validated
LLM fallback). Full design rationale — including why the LLM is scoped the
way it is — is in [`bitcoin_trading_agent_tutorial.md`](./bitcoin_trading_agent_tutorial.md).

## Tech stack

| Layer | Tools |
|---|---|
| Data | Coinbase Advanced Trade API, `pandas`, `requests` |
| Forecasting | `statsmodels` (ARIMA, AR, Exponential Smoothing), walk-forward CV |
| LLM integration | Anthropic / OpenAI APIs, JSON-schema response validation |
| Strategy & risk | Pure-Python rule engine, ATR-based position sizing |
| Config | Google Sheets (`gspread`), `.env` / `python-dotenv`, allowlisted overrides |
| Scheduling | `APScheduler` |
| Notifications | Telegram Bot API, Gmail SMTP |
| Deployment | Docker, docker-compose |

## What this project demonstrates

- **Time-series forecasting & model selection** — ARIMA/AR/ES compared via
  walk-forward cross-validation rather than a single in-sample fit.
- **Feature engineering under noisy, non-stationary data** — RSI, MACD,
  ATR, volume z-scores on a genuinely volatile asset.
- **Applied LLM integration with guardrails** — structured prompting,
  strict output validation, graceful degradation, and cost-aware call
  gating, instead of an unconstrained "let the model decide" pattern.
- **System design for reliability** — decoupled cadences, idempotent
  state writes, a security-conscious config allowlist, and defensive
  error handling around every external API call.
- **Rigorous validation methodology** — backtesting against the live code
  path, paper trading before any consideration of real capital, and an
  explicit accounting of what paper trading does *not* model (slippage,
  fees, partial fills).
- **Production engineering** — containerization, persistent state,
  structured logging, and a scheduler that isolates job failures from
  each other.

## Project structure

```
├── src/
│   ├── config/          # Config loading, secrets/strategy-param separation
│   ├── data/             # Daily + intraday market data and ATR
│   ├── ml/               # Forecasting, LLM regime classifier, threshold adaptation
│   ├── strategy/          # DCA rules, hybrid strategy orchestration
│   ├── broker/            # Simulated (paper) exchange
│   ├── notify/            # Telegram + Gmail reporting
│   ├── backtest/          # Backtesting engine
│   ├── jobs/              # Daily regime recompute job
│   └── main.py            # Single trading-cycle entry point
├── scheduler.py           # 24/7 orchestration (trading cycle, daily job, weekly report)
├── Dockerfile / docker-compose.yml
├── requirements.txt
├── .env.example
└── BitTradeAgent_updated.ipynb   # Full build + test notebook
```

## Getting started

```bash
git clone <this-repo>
cd <this-repo>
cp .env.example .env        # fill in your own API keys / tokens
pip install -r requirements.txt

# Run a single cycle
python -m src.main

# Or run continuously
python scheduler.py

# Or via Docker
docker compose up -d --build
docker compose logs -f
```

Leave `LLM_PROVIDER` blank in `.env` to run with rule-based thresholds only
— the system behaves identically with or without an LLM configured, which
was a deliberate design constraint, not an afterthought.

## Evaluation / current status

This repository trades against a **local, simulated paper broker** —
`portfolio.json` and `trades.csv` on disk, not a real exchange account.
That's intentional: the project prioritizes demonstrating sound
architecture, risk controls, and validation methodology over live P&L,
which is a poor metric to lead with with any strategy that hasn't run for
months across multiple market regimes. See the tutorial doc for the full
list of known limitations (ARIMA's weak signal on ~60 days of daily data,
paper trading's blind spots around slippage and fees) and the planned
next step of a real (non-paper) broker integration behind the same
interface as `paper_broker.py`.

## Author

Built by **Samuel Mugisha** as part of a data science portfolio spanning
time-series forecasting, applied ML, LLM-integrated systems, and
computer vision.

- GitHub: [github.com/samuelmugisha](https://github.com/samuelmugisha)
- LinkedIn: [samuelmugishadc](https://linkedin.com/in/samuelmugishadc)

## License

MIT — see `LICENSE`.
