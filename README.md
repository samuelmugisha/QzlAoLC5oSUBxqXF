
# Bitcoin Trading Agent

A smart bitcoin trading system designed to operate with minimal human supervision and continuously adapt to changing market conditions. 

The agent must dynamically manage budget allocation, shift between strategies, and make autonomous trading decisions while running 24/7.


# Project Objectives:
- Accept a configurable budget (e.g., $1K or $100K)
- Use Dollar-Cost Averaging (DCA) to accumulate more bitcoin when prices drop, distributing buys over time or price levels
- Implement an ATR-based stop-loss strategy to manage short-term trades and avoid excessive loss exposure
- Switch between different strategies (e.g., day trading, swing trading, value investing)
- Adapt continuously to market conditions, ideally with the help of a lightweight LLM
- Run 24/7 and deploy in a cloud environment
- Send Telegram notifications for each trade made
- Send a weekly email report every Monday at 9:00AM via Gmail


