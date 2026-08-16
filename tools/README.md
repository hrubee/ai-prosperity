# AI Prosperity — Auditing, Verification & Backtesting Toolkit

This directory contains standalone testing, verification, and quantitative research scripts used to monitor live execution, audit wallet balances and fills, and backtest strategy variations.

---

## 📁 `audit/` — Live Account, Wallet & PnL Auditing

| Script | Description |
| :--- | :--- |
| **`audit_acc2_fibvol_only.py`** | Audits realized trades, win rates, net PnL, and fee breakdown specifically for Account 2 FibVOL strategy. |
| **`audit_coindcx_real.py`** | Real-time CoinDCX account reconciliation matching live exchange fills against strategy logs. |
| **`audit_primary_pnl.py`** | Audits Primary Account live PnL, open positions, and margin utilization. |
| **`audit_r_multiple.py`** | Calculates empirical R-multiple distributions, expectancy, and profit factor from actual trade fills. |
| **`audit_real_hei_pnl.py`** | Deep audit of specific trade executions and slippage analysis. |
| **`check_all_positions_margin_type.py`** | Verifies isolated vs cross margin modes across all open futures positions. |
| **`check_coindcx_all_balances.py`** | Queries all available INR and USDT wallets, collateral, and locked margins. |
| **`fetch_coindcx_trades.py`** | Fetches raw order execution records, timestamps, and fees directly via CoinDCX API. |
| **`fetch_live_coindcx_upnl.py`** | Real-time unrealized PnL and active margin monitor for open positions. |
| **`fetch_open_positions.py`** | Queries currently open positions across all connected trading accounts. |
| **`reconcile_coindcx.py`** | Reconciles wallet balance deltas against executed trade history. |
| **`explore_coindcx_wallets.py`** | Introspects sub-account wallet structures and asset distribution. |
| **`find_acc2_futures_balance.py`** | Dedicated balance probe for Secondary Account futures wallet. |
| **`test_coindcx_orders_api.py`** | Validates order creation, bracket endpoints, and SL/TP trigger syntax. |
| **`test_live_wallet_read.py`** | Verifies real-time read latency and response formatting for account balance endpoints. |

---

## 📊 `backtest/` — Backtesting & Quantitative Sweeps

| Script | Description |
| :--- | :--- |
| **`backtest_fibvol_dynamic_1m.py`** | High-fidelity minute-granularity backtest of the active FibVOL Long strategy (dynamic green updates & red cancellations). |
| **`backtest_short_fixed_rr.py`** | Mean Reversion Shorting strategy backtest with 9 EMA crossover and 70% retracement targets. |
| **`backtest_timeframes_fibvol.py`** | Multi-timeframe comparative backtest sweep (1m, 5m, 15m, 30m, 1h, 4h). |
| **`sweep_tp_levels.py`** | Grid search across Take Profit (1:1 to 1:10 RR) parameters. |
| **`sweep_fibvol_entry_levels.py`** | Sweep across Fibonacci Entry (0.4 to 0.7) and Stop Loss (0.6 to 0.9) levels. |
| **`sweep_fibvol_vol_thresholds.py`** | Sensitivity analysis across volume spike multipliers ($10\times$ to $50\times$). |
| **`sweep_trailing_sl.py`** | Evaluates activation thresholds and trailing distance parameters for dynamic stop management. |
| **`simulate_deployed_fibvol_trades.py`** | Step-by-step 1m replay simulating real live execution behavior. |
| **`fast_backtest_official_fibvol.py`** | Fast vectorized backtest engine for broad universe scans. |
| **`detailed_metrics_backtest.py`** | Calculates Sharpe ratio, maximum drawdown, win streaks, and loss distributions. |

