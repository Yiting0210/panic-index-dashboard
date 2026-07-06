# Market Sentiment & Panic Index Dashboard

Repository: `Yiting0210/panic-index-dashboard`

> **Exploring Market Asymmetry**: Can a Composite Panic Index (VIX + CNN Fear & Greed) identify tactical entry and exit opportunities across major indices and the Semiconductor sector (2021-2026)?

**Live Demo**: [https://panic-index-dashboard-gvjixdtgeazuqzzm3kqqv4.streamlit.app](#)

---

## Overview

An interactive Streamlit dashboard built around a **Composite Panic Index**, combining VIX and CNN Fear & Greed data to study market stress, sentiment extremes, and forward returns across major indices and semiconductor ETFs.

The project has two main pages:

- **Threshold Strategy**: the main deployable strategy page. It uses Panic Index thresholds as a regime-based accumulation and risk-management rule, scaling exposure during panic-zone periods and de-risking during greed regimes.
- **Peak Detection & Signal Validation**: a research and validation page. It compares historical Panic Index peaks and causal panic-reversal signals against subsequent forward returns without presenting them as equivalent live trading strategies.

**Key Finding**: The Panic Index shows an asymmetric historical pattern: panic regimes have tended to precede stronger medium-term forward returns than greed regimes, while greed has been less reliable as a short or exit signal during sustained bull markets.

---

## Features

- **Threshold Strategy**: regime-based allocation using Panic Index buy-zone and sell-zone thresholds
- **Interactive Backtest**: position sizing strategy vs Buy & Hold with adjustable allocation parameters
- **Forward Return Analysis**: historical return distributions and statistical summaries after signal dates
- **scipy.find_peaks Historical Validation**: hindsight labels used to test whether Panic Index peaks historically aligned with favorable forward returns
- **RealTimePeakDetector Causal Signal Analysis**: a past/current-data-only panic-reversal confirmation signal for tactical research
- **Interactive Controls**: date range filter, index selector, detection parameters, and analysis horizons

---

## Composite Panic Index

```text
vix_norm    = (VIX - VIX_min) / (VIX_max - VIX_min) x 100
fg_fear     = 100 - Fear_Greed_Score
panic_index = vix_norm x 0.5 + fg_fear x 0.5
```

| Signal | Threshold | Basis |
|--------|-----------|-------|
| Buy Zone | Panic Index > 64 | 95th percentile of 5-year history |
| Sell Zone | Panic Index < 15 | 5th percentile of 5-year history |

---

## Signal Design

The dashboard separates the Panic Index signal from the different ways it can be used or studied:

- **Threshold Strategy**: the primary deployable rule. It treats Panic Index extremes as market regimes, not isolated one-day forecasts. Panic-zone days are accumulation / exposure days; greed-zone days are de-risking days.
- **scipy.find_peaks**: a hindsight historical validation label. It uses the full historical series to identify local maxima after the fact, so it should not be interpreted as a live trading signal.
- **RealTimePeakDetector**: a causal panic-reversal confirmation signal. It only uses past/current data, waiting for panic to spike and then retreat before flagging a signal.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Streamlit |
| Visualization | Plotly |
| Data | Static CSV/Excel-style data files |
| Language | Python 3.12 |
| Deployment | Streamlit Community Cloud |

---

## Project Structure

```text
panic-index-dashboard/
├── Home.py                               # Streamlit Cloud entry point and home page
├── pages/
│   ├── 1_💠_Threshold_Strategy.py        # Main regime-based strategy page
│   └── 2_🏔️_Peak_Detection.py           # Peak detection and signal validation page
├── utils/
│   ├── data_loader.py                    # Static data loading and filtering
│   ├── charts.py                         # Plotly chart builders and render helpers
│   ├── signals.py                        # Signal preparation and masks
│   ├── peak_detection.py                 # scipy and causal peak detection logic
│   ├── analysis.py                       # Backtest verdicts and comparison tables
│   └── sidebar.py                        # Streamlit sidebar controls
├── data/                                 # Static market sentiment and price data
├── requirements.txt                      # Runtime dependencies for Streamlit
├── requirements-dev.txt                  # Development and CI dependencies
└── README.md
```

---

## Local Setup

```bash
git clone https://github.com/Yiting0210/panic-index-dashboard.git
cd panic-index-dashboard
pip install -r requirements.txt
streamlit run Home.py
```

---

## Data Sources

For Streamlit Cloud simplicity, the dashboard currently reads CSV/Excel-style static data from the `data/` directory at runtime.

- **Fear & Greed Index (2021-2022)**: [MacroMicro](https://sc.macromicro.me/charts/50108/cnn-fear-and-greed)
- **Fear & Greed Index (2023-2026)**: CNN internal endpoint (`production.dataviz.cnn.io`)
- **Price data**: Yahoo Finance via `yfinance`: QQQ, SMH, SPX, DJI, VIX
- **Missing values**: Market holidays handled via linear interpolation

---

## Key Results (Full Period: May 2021 - Apr 2026)

**Price Signal Map:**
- Threshold buy-zone observations (Panic Index > 64) cluster around major stress regimes such as the 2022 bear market and 2025 tariff shock.
- These are panic-regime accumulation days, not independent single-day trade recommendations.
- Sell-zone observations (Panic Index < 15) appear during sustained greed periods in the 2023-2024 bull market.

**Forward Return Analysis (full 5-year period):**
- Extreme panic regimes showed historically favorable forward-return distributions across 1-week to 3-month horizons.
- Extreme greed regimes were less reliable as exit or short signals because prices often continued rising during sustained uptrends.
- **The Panic Index appears asymmetric in this historical sample, but the result should not be interpreted as guaranteed alpha.**

**Backtest vs Buy & Hold:**
- Strategy reduces maximum drawdown by ~56% (-15% vs -35%)
- Buy & Hold outperforms on absolute return in this bull market period
- Strategy is more suitable for **risk-averse investors** who prioritize capital preservation

**Correlation Analysis:**
- Day-ahead predictive correlation near zero, consistent with Efficient Market Hypothesis
- Medium-term regime identification (1-week to 3-month) shows significant signal effectiveness

---

## Limitations

- The dashboard currently uses a static historical dataset for demo and research simplicity.
- `scipy.find_peaks` is hindsight-only and should not be treated as a deployable trading signal.
- RealTime forward-return analysis uses the underlying index/ETF as a directional proxy, not a full options pricing backtest.
- Full-sample VIX normalization is suitable for historical dashboard analysis, but live deployment should use fixed, rolling, or expanding calibration.
