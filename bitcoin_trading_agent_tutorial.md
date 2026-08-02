# Learning the Bitcoin trading agent: a tutorial for beginners

This is written for someone who has never built a trading agent before.
It covers the concepts you need first, then walks through the actual
codebase piece by piece, explaining not just *what* each part does but
*why* it exists and what would go wrong without it.

---

## Part 1: The concepts, before the code

Skip this part if you already know what ATR and drawdown mean. If you
don't, the code will look like arbitrary math without it.

### 1.1 What "algorithmic trading" actually means

An algorithmic trading system is just a program that decides *when* to
buy or sell an asset, based on rules, instead of a human staring at a
chart. The hard part is almost never "how do I place an order" — it's:

- **What triggers a decision?** (a price move, an indicator crossing a
  threshold, a model's prediction)
- **How much do I risk on each decision?** (position sizing)
- **When do I admit I was wrong and get out?** (stop-losses)
- **How do I know the rules actually work** *before* risking real
  money? (backtesting)

Everything in this project is one of those four things.

### 1.2 Dollar-Cost Averaging (DCA)

DCA is the simplest strategy that actually works reasonably well for a
volatile asset like Bitcoin: instead of trying to time the market, you
buy a fixed dollar amount at regular intervals, or whenever the price
drops by some percentage. The idea is that you don't need to predict
anything — you just need discipline. If the price keeps dropping,
you're buying more Bitcoin per dollar each time (a lower average cost);
if it recovers, you're already holding.

In this project, `DCAStrategy.should_buy()` checks three things before
approving a buy:
1. Has price dropped enough since the last buy?
2. Has enough *time* passed since the last buy? (so a single crash
   doesn't trigger ten buys in one hour)
3. Is there budget left?

### 1.3 Average True Range (ATR) — measuring volatility

ATR answers one question: **"how much does this asset typically move
in a given period?"** It's computed from the "true range" of each
candle (the largest of: high-low, high-to-previous-close, and
low-to-previous-close), averaged over N periods (14 is the common
default).

Why it matters: a fixed-dollar stop-loss ("sell if I lose $500") makes
no sense for Bitcoin, because $500 might be nothing on a day BTC moves
$3,000, and huge on a quiet day. ATR lets your stop-loss *adapt* to
current volatility: `stop = entry_price - k × ATR`. If `k = 1.5` and
ATR is $800, your stop sits $1,200 below your entry — wide enough to
survive normal noise, tight enough to actually protect you.

### 1.4 Stop-loss and "swing" trades

A **swing trade** here means a shorter-term, opportunistic entry
(distinct from the patient, always-on DCA buys) that gets a hard exit
point the moment it's opened. If price falls to that stop level, the
position is closed automatically — no judgment call, no "it'll bounce
back." This is what keeps one bad trade from becoming a catastrophic
one.

### 1.5 Portfolio-level risk (drawdown) — the circuit breaker

**Drawdown** is how far your portfolio has fallen from its peak value,
as a percentage. Per-trade stop-losses protect you from one bad trade;
a drawdown-based circuit breaker protects you from a *sequence* of bad
decisions, a broken assumption, or a market regime the strategy simply
wasn't designed for. If total portfolio value falls 25% from its
high-water mark, this system pauses all new entries — existing
stop-losses still fire (those protect capital), but nothing new opens
until you've reviewed what happened.

This is arguably the single most important piece of a 24/7 unattended
system. Everything else can be wrong in small ways and you'll lose a
little money; this being wrong (or silently broken) means you can lose
a lot.

### 1.6 Backtesting and paper trading — testing without risking money

- **Backtesting** replays historical price data through your strategy
  logic to see what it *would have* done. Fast, cheap, but has a
  well-known failure mode: it's easy to accidentally build a strategy
  that looks great on the specific history you tested against and
  falls apart on anything new (overfitting).
- **Paper trading** runs your strategy against *live* prices but with
  simulated money — no backtesting shortcuts, no hindsight, but also no
  real financial risk. This project's `paper_broker.py` is exactly
  this: a fake exchange backed by a local JSON file.

The sane order is: backtest → paper trade for a while → only then
consider real money. This project deliberately stops at paper trading.

### 1.7 Regime classification — "what kind of market is this?"

Markets behave differently depending on context: trending (steadily up
or down), ranging (bouncing in a band), or breaking out (sudden, sharp
move). A strategy tuned for one regime can lose money in another — DCA
works great in a trending-down-then-recovering market and does nothing
useful in a flat, ranging one; a swing strategy wants volatility and
gets chopped up in a quiet range.

**Regime classification** is the attempt to label current conditions
so the strategy can adapt — tighten or loosen its rules depending on
context. This project does it two ways: a statistical forecast (ARIMA)
and, optionally, an LLM call — more on that below.

### 1.8 Why "agent," and what's actually intelligent here

Calling this an "agent" doesn't mean anything mystical — it means a
system that observes (market data), decides (strategy rules), and acts
(places orders, sends alerts) on a loop, without a human in that loop.
The "intelligence" in most working trading systems is mostly
*engineering discipline* — clean risk controls, good logging, boring
reliable code — not clever prediction. The LLM's role here is
deliberately narrow for exactly this reason: it's one input among
several, not the decision-maker.

---

## Part 2: How the pieces fit together

Before diving into individual files, hold this picture in your head —
it's the thing every design decision below traces back to:

**Two clocks, one state, layered safety.**

- **Fast clock (every 30 minutes)**: check price, check risk, decide on
  DCA/swing/stop-loss, act, notify.
- **Slow clock (once a day)**: re-assess the broader market regime,
  update the *parameters* the fast clock uses (not the fast clock's
  logic itself).
- **Shared state**: a handful of JSON/CSV files (`portfolio.json`,
  `trades.csv`, `active_trades.json`, `runtime_overrides.json`) that
  every component reads and writes, so a restart doesn't lose history.
- **Layered safety**: DCA has no ATR risk; swing trades have a
  per-trade ATR stop; the whole portfolio has a drawdown circuit
  breaker. Three independent layers, each protecting against a
  different failure mode.

The trading-cycle diagram above is the fast clock. Here's the slow
clock and the layers it feeds:

```
Daily regime job (00:05 UTC)
  → fetch daily candles → compute daily ATR → run ARIMA forecast
  → (optional) LLM regime call → write runtime_overrides.json
                                              │
                                              ▼
Every 30-min trading cycle reads runtime_overrides.json
  for: enable_swing, atr_k_stop, dca_drop_percent
```

---

## Part 3: Walking through the code, module by module

This follows the order in the updated notebook. For each module: what
it does, why it's built that way, and the one thing worth remembering.

### `src/config/config_manager.py` — configuration and secrets

Two categories of configuration, kept strictly separate:
- **Secrets** (API keys, tokens) — loaded once from a local `.env`
  file, never touched by anything else.
- **Strategy parameters** (budget, DCA drop %, ATR multiplier) — loaded
  from `.env` as defaults, then optionally overridden hourly from a
  Google Sheet, so you can tune the strategy without redeploying code.

**The one thing worth remembering**: the Sheet can only override an
explicit allowlist of keys (`SHEET_OVERRIDABLE_KEYS`). This exists
because early versions of this pattern let *any* key in the Sheet
overwrite the config dict — including secrets. If you ever extend this
system, keep that allowlist discipline; it's a cheap habit that
prevents an expensive mistake.

### `src/data/price_data.py` — daily market data

Fetches daily OHLC candles from Coinbase and computes ATR from them.
This feeds the *slow clock* only (the daily regime job) — nothing in
the 30-minute cycle uses this module's ATR anymore.

### `src/data/intraday_price_data.py` — intraday market data (new)

Same idea, but pulls 30-minute candles and computes ATR from those.
This feeds the *fast clock* — it's what sizes a new swing trade's stop
distance. The reason these are two separate modules instead of one
with a parameter: they answer genuinely different questions ("how
volatile has it been the last few hours" vs. "what's the character of
this week's market"), and keeping them separate makes that distinction
visible in the code instead of buried in an argument default.

### `src/strategy/dca_strategy.py` — the DCA rules

A self-contained class with no side effects beyond its own internal
state (`purchases`, `total_spent`, `total_btc`). Notice it doesn't call
any exchange or broker — it only *decides*. This separation (decide vs.
act) is what makes it testable: you can call `should_buy()` a thousand
times with synthetic prices and never touch a real API.

### `src/ml/forecaster.py` — statistical price forecasting

Uses ARIMA (a classic time-series model — think "a slightly smarter
moving average that also models how the series tends to correct
itself") to predict the *direction and rough size* of the next daily
move. Returns two numbers: `pred_return` (predicted % change) and
`pred_strength` (how confident, roughly, that prediction is). It's
deliberately simple — this isn't meant to be a sophisticated forecaster,
it's meant to give the threshold adapter *something* numeric to react
to.

**Worth knowing**: ARIMA on ~60 days of daily Bitcoin data is a fairly
weak signal. Don't mistake "the code runs" for "the forecast is
reliable" — that's exactly the kind of overconfidence backtesting
exists to check.

### `src/ml/llm_regime.py` — the LLM's actual job (new)

This is worth understanding carefully, because it's easy to build LLM
integrations badly.

**What it does**: takes a handful of already-computed numbers (RSI,
ATR-as-percent-of-price, the ARIMA forecast) and asks an LLM to
classify the regime (`trending_up` / `trending_down` / `ranging` /
`volatile_breakout`) and flag whether current conditions look like a
good swing-entry setup. Nothing more.

**What it deliberately does *not* do**: see raw price candles, place a
trade, or set a numeric threshold directly. Its output is validated
against a strict schema before anything downstream can use it, and if
the call fails, times out, or returns something malformed, the system
falls back to a neutral default — the trading loop never stalls or
breaks because an API call had a bad day.

**Why this design, and not "let the LLM decide"**: an LLM is good at
synthesizing a handful of signals into a qualitative judgment
("this looks choppy") — a genuinely hard thing to write clean rules
for. It's *not* something you want directly wired to "spend money,"
because LLMs can be confidently wrong in ways that don't fail loudly.
Scoping it to "recommend a label, which can only ever tighten one
boolean flag within existing numeric bounds" gets the benefit (better
regime awareness) without the risk (an LLM hallucination moving real
money).

### `src/ml/threshold_adapter.py` — turning forecasts into parameters

Two functions:
- `adapt_thresholds()` — pure rule-based math. Takes the forecast and
  ATR, nudges `atr_k_stop` and `dca_drop_percent` within fixed bounds
  (`[1.0, 2.5]` and `[1.0, 8.0]`), and decides if swing trades are
  eligible at all today.
- `adapt_thresholds_with_llm()` — calls the above, then layers the LLM
  regime call on top. The LLM can only make `enable_swing` *more*
  restrictive, never override the numeric clamps.

This is the clearest example in the whole project of a general
principle worth internalizing: **let deterministic code own anything
where a bug or a bad prediction has a hard financial consequence
(the actual thresholds); let softer, harder-to-verify components
(LLMs, statistical forecasts) only ever narrow what's already allowed.**

### `src/broker/paper_broker.py` — the simulated exchange

A local JSON file pretending to be an exchange. `place_market_buy()`
and `place_market_sell()` update `portfolio.json` and append a row to
`trades.csv`. This is genuinely all you need to validate strategy logic
against live prices without financial risk — and it's built behind the
same function signatures a real broker integration would use, so
swapping in the Coinbase API later means writing a new module with the
same interface, not rewriting the strategy code.

### `src/strategy/strategy_manager.py` — the orchestrator

Ties DCA, swing entries, stop-losses, and the portfolio risk check
together into one `evaluate_hybrid()` call per cycle. Read
`_check_portfolio_risk()` first — it's the circuit breaker from section
1.5, implemented. Then `evaluate_hybrid()` — notice the order: risk
check happens *before* anything else, and if it fails, the function
returns immediately with `risk_pause: True`. Nothing after that point
in the function ever executes on a paused portfolio. That ordering is
not an accident; it's the whole point of a circuit breaker.

### `src/notify/telegram.py` and `src/notify/gmail_report.py` — visibility

You cannot supervise a 24/7 system if it doesn't tell you what it's
doing. Telegram fires synchronously on every trade and every risk
alert; Gmail sends a weekly rollup. Notice `gmail_report.py` reads
`llm_regime` and `llm_rationale` from the overrides file if present —
this is what turns "the system changed its swing eligibility today"
from an invisible internal state change into something you can
actually read and evaluate in your inbox.

### `src/backtest/engine.py` — validating before you trust it

Replays historical daily candles through the exact same
`StrategyManager` used live. This matters: a backtester that
reimplements strategy logic separately from the live code is a classic
trap — the two implementations drift apart, and you end up validating
a strategy you're not actually running. Here, both paths call the same
`evaluate_hybrid()`.

### `src/jobs/daily_regime_job.py` — the slow clock

Ties `price_data.py` (daily candles), `forecaster.py` (ARIMA), and
`threshold_adapter.py` (LLM + rules) together, once a day, and writes
the result for the fast clock to read. If you only read one file to
understand "how does the daily/intraday split actually work in code,"
read this one.

### `src/main.py` — the fast clock, one cycle

This is the file the diagram at the top of this document describes.
Read it top to bottom once — it's short, and every other module exists
to support one function in this file: `run_once()`.

### `scheduler.py`, `Dockerfile`, `docker-compose.yml` — making it run forever

`scheduler.py` is what turns "a function I can call" into "a system
that runs unattended." It schedules three things on independent
cadences (trading cycle, daily regime job, weekly report) and — this is
the part worth noticing — wraps each one in its own try/except, so a
failure in one job (say, the Gmail job hitting a bad SMTP day) can
never take down the trading cycle. Docker just packages all of this
with its dependencies so it runs the same way on your laptop and on a
cloud server.

---

## Part 4: Running it yourself

1. **In the notebook**: run cells top to bottom. Each `%%writefile`
   cell writes a real file to `src/`; each test cell after it exercises
   that file with a small example. This is the fastest way to see each
   piece work in isolation.
2. **As a continuous local process**: `python scheduler.py` (after
   `pip install -r requirements.txt` and creating a `.env` from
   `.env.example`). Watch the log output — it prints every cycle,
   every decision, every skip reason.
3. **In Docker**: `docker compose up -d --build`, then
   `docker compose logs -f`. This is the version that should run for
   your week-long evaluation log.

Start with `LLM_PROVIDER` blank in `.env`. Confirm the system behaves
sensibly with rule-based thresholds only — that's your baseline. Only
then set an API key and compare behavior with the LLM regime call
active. This gives you a real before/after, which is exactly the kind
of evidence worth including in an evaluation write-up.

---

## Part 5: Things that will bite you if you don't know them going in

- **Overfitting a backtest.** If you tune `DCA_DROP_PERCENT` and
  `ATR_MULTIPLIER` by repeatedly backtesting against the same 60 days
  of data until the numbers look great, you've fit noise, not signal.
  Test on a period, then validate on a *different* period you didn't
  tune against.
- **Silent failures are worse than loud ones.** The three bugs found in
  review (a broken import inside a bare `except: pass`, a mismatched
  file path, a missing f-string) were all silent — the system kept
  running, just wrong. When you extend this code, prefer letting
  exceptions log loudly over swallowing them, especially anywhere near
  money or alerts.
- **Timeframe mismatches are easy to introduce by accident.** The
  ATR-granularity issue this project just fixed is a general pattern:
  any time you compute a number on one clock and use it on another,
  write down *why* that's okay, or it probably isn't.
- **Paper trading isn't a full dress rehearsal.** It doesn't model
  slippage (the price moving between your decision and your fill),
  exchange fees, or partial fills. Real execution will perform slightly
  worse than paper trading suggests — budget for that gap mentally
  before switching on real capital.
- **A drawdown circuit breaker only helps if its alert actually
  arrives.** This is exactly what was broken in the original code.
  Test your alerting path deliberately (force a risk-pause condition
  in a test run) rather than assuming it works because the code looks
  right.

---

## Glossary

| Term | Meaning |
|---|---|
| DCA | Dollar-cost averaging — buying fixed amounts on a schedule or on dips |
| ATR | Average True Range — a volatility measure used to size stop-losses |
| Stop-loss | A predefined exit price that closes a losing position automatically |
| Drawdown | Percentage decline from a portfolio's peak value |
| Backtest | Simulating a strategy against historical data |
| Paper trading | Simulating a strategy against live prices with fake money |
| Regime | The general character of current market conditions (trending, ranging, volatile) |
| RSI / MACD | Momentum indicators — RSI measures overbought/oversold; MACD measures trend momentum via moving-average differences |
| Slippage | The gap between the price you intended to trade at and the price you actually got |
| Circuit breaker | A system-wide safeguard that halts activity when a risk threshold is breached |

## Where to go deeper

- **DCA/ATR/stop-loss mechanics**: Investopedia's technical-indicator
  articles are a solid, unbiased starting point for every indicator
  mentioned above.
- **Backtesting pitfalls**: search specifically for "backtest
  overfitting" and "look-ahead bias" — the two most common ways people
  fool themselves.
- **Time-series forecasting**: any introductory treatment of ARIMA will
  explain what this project's `forecaster.py` is actually doing
  mathematically.
- **Building this further**: the natural next steps, in order of value,
  are a real (non-paper) broker integration behind the same interface
  as `paper_broker.py`, a daily portfolio-value snapshot so the weekly
  report's P&L is real, and a small dashboard reading the same JSON/CSV
  files this system already writes.
