# Two-year replay status — 28 September 2026

Command attempted:

`python -m bot.main --backtest --from-date 2024-09-28 --date 2026-09-27`

The run stopped before its first trading session (30 September 2024): `No point-in-time Nifty 200 membership snapshot within 31 days of 2024-09-30`. Separately, a Yahoo Finance historical download probe returned a rate-limit error. The repository does not contain historical constituent or broker eligibility archives, and no fabricated or current-membership substitute was used.

**Result:** no valid market backtest, no return or trade statistics. The workflow and same execution engine are provided. Populate historical membership and weekly eligibility archives from dated sources and use a reliable historical OHLCV/corporate-action provider before interpreting any replay. The five deterministic engine tests pass; they verify software behavior, not investment performance.
