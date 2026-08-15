# 📊 June 2026 Continuous 1-Minute Backtest Report (135 Parameter Combinations)

## 📌 Executive Summary

An independent, continuous 1-minute candle granularity backtest was executed across the entire month of June 2026 using the SQLite database (`/root/data/june_2026_1m.db` containing **15,313,290 continuous 1m candles** for **357 altcoin symbols**).

A total of **135 strategy parameter combinations** were evaluated across:
- **Fibonacci Entry Levels**: `0.600`, `0.700`
- **Fibonacci Stop Loss Levels**: `0.700`, `0.800`
- **Volume Spike Multipliers**: `10x`, `15x`, `20x`, `25x`, `30x`, `35x`, `40x`, `45x`, `50x`
- **Trailing Stop Loss Distances**: `1.0R`, `1.5R`, `2.0R`, `2.5R`, `3.0R` *(with instant 1.0R breakeven activation)*

---

## 🏆 #1 Champion Strategy Combination

> [!TIP]
> **Optimal Strategy Parameter Setup (#1 Ranked)**:
> - **Entry Fibonacci Level**: **`0.600`** *(Entry at 60% retracement from 15m candle high)*
> - **Stop Loss Fibonacci Level**: **`0.700`** *(Tight 10% risk bracket: $0.700 - 0.600 = 0.100 \times \text{Candle Range}$)*
> - **Volume Spike Threshold**: **`10.0x`** *(10-hour SMA baseline volume multiplier)*
> - **Trailing Stop Loss**: **`1.0R`** *(Instant activation @ +1.0R profit, trailing 1.0R behind peak)*
> - **Executed Trades**: **675 Trades**
> - **Win Rate**: **`59.6%`**
> - **Profit Factor**: **`1.71`**
> - **NET JUNE REALIZED PROFIT**: **`+$1,932.56 USDT` (`+₹1,71,031.79 INR`)** 🚀

---

## 📊 Top 15 Ranked Strategy Combinations

Below is the complete leaderboard of the top 15 strategy parameter combinations sorted by **Net Realized PnL (INR)**:

| Rank | Entry Fib | SL Fib | Volume Spike | Trailing SL | Trades | Win Rate % | Avg R | Profit Factor | Net PnL (USDT) | Net PnL (INR) |
|---|---|---|---|---|---|---|---|---|---|---|
| **#1** 🏆 | **`0.600`** | **`0.700`** | **`10.0x`** | **`1.0R`** | **675** | **59.6%** | **+0.29R** | **`1.71`** | **`+$1,932.56`** | **`+₹1,71,031.79 INR`** |
| **#2** 🥈 | **`0.700`** | **`0.800`** | **`10.0x`** | **`1.0R`** | **693** | **58.3%** | **+0.25R** | **`1.60`** | **`+$1,703.33`** | **`+₹1,50,744.79 INR`** |
| **#3** 🥉 | **`0.600`** | **`0.700`** | **`10.0x`** | **`1.5R`** | **681** | **44.3%** | **+0.21R** | **`1.46`** | **`+$1,397.46`** | **`+₹1,23,675.34 INR`** |
| **#4** | **`0.600`** | **`0.700`** | **`10.0x`** | **`2.0R`** | **679** | **38.1%** | **+0.20R** | **`1.39`** | **`+$1,358.93`** | **`+₹1,20,265.38 INR`** |
| **#5** | **`0.600`** | **`0.700`** | **`10.0x`** | **`2.5R`** | **679** | **33.4%** | **+0.18R** | **`1.30`** | **`+$1,231.20`** | **`+₹1,08,961.60 INR`** |
| **#6** | **`0.700`** | **`0.800`** | **`10.0x`** | **`1.5R`** | **696** | **43.5%** | **+0.14R** | **`1.31`** | **`+$994.14`** | **`+₹87,981.26 INR`** |
| **#7** | **`0.600`** | **`0.700`** | **`10.0x`** | **`3.0R`** | **677** | **30.0%** | **+0.10R** | **`1.14`** | **`+$645.14`** | **`+₹57,094.93 INR`** |
| **#8** | **`0.700`** | **`0.800`** | **`10.0x`** | **`2.0R`** | **693** | **37.1%** | **+0.08R** | **`1.15`** | **`+$559.27`** | **`+₹49,495.41 INR`** |
| **#9** | **`0.600`** | **`0.700`** | **`15.0x`** | **`1.0R`** | **232** | **54.7%** | **+0.13R** | **`1.28`** | **`+$291.38`** | **`+₹25,787.26 INR`** |
| **#10** | **`0.700`** | **`0.800`** | **`15.0x`** | **`1.0R`** | **222** | **59.0%** | **+0.12R** | **`1.29`** | **`+$257.96`** | **`+₹22,829.80 INR`** |
| **#11** | **`0.600`** | **`0.700`** | **`20.0x`** | **`1.0R`** | **87** | **52.9%** | **+0.29R** | **`1.62`** | **`+$248.21`** | **`+₹21,966.44 INR`** |
| **#12** | **`0.600`** | **`0.700`** | **`15.0x`** | **`1.5R`** | **88** | **44.3%** | **+0.22R** | **`1.45`** | **`+$195.07`** | **`+₹17,263.55 INR`** |
| **#13** | **`0.600`** | **`0.700`** | **`20.0x`** | **`2.0R`** | **88** | **39.8%** | **+0.09R** | **`1.16`** | **`+$78.98`** | **`+₹6,989.54 INR`** |
| **#14** | **`0.600`** | **`0.700`** | **`20.0x`** | **`2.5R`** | **88** | **31.8%** | **+0.09R** | **`1.14`** | **`+$77.40`** | **`+₹6,850.01 INR`** |
| **#15** | **`0.600`** | **`0.700`** | **`15.0x`** | **`1.5R`** | **233** | **42.1%** | **+0.03R** | **`1.07`** | **`+$77.20`** | **`+₹6,831.77 INR`** |

---

## 💡 Key Empirical Insights

### 1. The Power of 10.0x Volume Spikes + 1.0R Trailing Stop Loss
- A **10.0x Volume Spike** combined with an **instant 1.0R Trailing Stop Loss** achieves the highest net performance.
- When price reaches $+1.0R$ profit, the Stop Loss is immediately moved to **Breakeven ($0.0R$)**, eliminating loss risk on trades that move $+1.0R$ in your favor.
- This produces a **59.6% Win Rate** and **+₹1,71,031.79 INR net profit** across 675 trades in June.

### 2. Fib Retracement Tightness (0.600 Entry / 0.700 SL vs 0.700 Entry / 0.800 SL)
- Entry at **0.600 Fib** with SL at **0.700 Fib** creates a tight 10% risk bracket ($0.700 - 0.600 = 0.100$).
- This tight risk bracket inflates the R-Multiple return on quick momentum pushes, boosting average trade return to **+0.29R per trade**.

---

## 🛠️ Backtest Methodology & Data Integrity

- **Database**: `/root/data/june_2026_1m.db` (15.3 million 1m candles, 357 symbols).
- **Execution Granularity**: Every trade was simulated **candle-by-candle at 1-minute resolution** for entry fills, peak tracking, trailing SL step-ups, and exit triggers.
- **Slippage & Exchange Fees**: Subtracted `0.05R` per trade to simulate live exchange maker/taker fee structures.
- **Execution Time**: The 135-combination grid search completed in **407.67 seconds** on the VPS.
