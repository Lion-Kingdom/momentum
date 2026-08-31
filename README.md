# Quantitative Momentum & Gamma Regimes Reporting Pipeline

An automated, end-to-end quantitative options trading pipeline that ingests daily market data, calculates Gamma Regimes, and leverages the Gemini AI API to generate actionable options strategies. 

Orchestrated via GitHub Actions, this suite runs twice daily to extract dealer positioning, filter out market noise, and deliver a comprehensive "Deep Dive Market Report" directly to your inbox.

## 🧠 Core Architecture & Features

*   **Automated Data Retrieval:** Securely pulls raw Gamma Exposure (GEX) and momentum data from centralized Google Sheets using Google Cloud Service Accounts.
*   **OHLCV Aggregation & Smart Pruning:** Utilizes `yfinance` to append daily price action and volume metrics to the master dataset. A built-in rolling 5-day retention policy ensures the data footprint remains lightweight and performant.
*   **AI Precision Filtering:** Slices the dataset to remove "Stand Aside" (conflicting signal) tickers, saving API compute time and reducing noise. 
*   **Actionable Intelligence Engine:** Feeds active Negative/Positive Gamma setups into the Gemini 1.5 Flash API. The model mathematically relates current price action to Call Walls and Put Walls to output specific options structures (e.g., Bull Put Credit Spreads, Long Calls) with precise strike prices and delta parameters.
*   **Seamless Charting Integration:** Automatically compiles a clean, comma-separated watchlist of all active tickers at the bottom of the generated report for quick importing into ThinkOrSwim, TradingView, or other charting software.
*   **Automated CI/CD Workflow:** Entirely serverless execution managed by GitHub Actions (`daily_momentum.yml`). Triggered via `repository_dispatch` from an external crontab (running at 9:00 AM and 1:30 PM EST), ensuring accurate, delay-free execution. The workflow automatically commits updated, pruned datasets back to the repository for historical auditing.

## 🛠️ Technology Stack

*   **Language:** Python 3.10
*   **Data Processing:** Pandas, NumPy, SciPy
*   **Market Data:** yfinance, PyFinViz
*   **AI / LLM:** Google Generative AI SDK (Gemini 1.5 Flash)
*   **Automation:** GitHub Actions, Git
*   **Delivery & Integration:** `smtplib` (Email Dispatch), `gspread` / `oauth2client` (Google Sheets API)

## ⚙️ Repository Structure

*   `/momentum_suite/momentum_revised.py`: Core momentum filtering and technical indicator calculations.
*   `/momentum_suite/gamma_screener.py`: Extracts GEX data, calculates support/resistance zones, and generates the baseline CSV log.
*   `ai_reporting_pipeline.py`: The AI engine that merges OHLCV data, enforces the 5-day retention policy, prompts Gemini for trade structures, and dispatches the email report.
*   `.github/workflows/daily_momentum.yml`: The YAML configuration dictating the automated pipeline, dependencies, and environment variable mapping.

## 🔐 Configuration & Secrets

To fork and run this pipeline locally or in your own GitHub environment, the following Repository Secrets must be configured:

*   `GCP_SA_KEY`: JSON credentials for the Google Cloud Service Account (to access GEX Sheets).
*   `GEMINI_API_KEY`: API key for Google AI Studio.
*   `EMAIL_USER`: The dispatching email address.
*   `EMAIL_PASS`: The App Password for the dispatching email.

## 🚀 Execution

This pipeline relies on an external cron job to bypass standard GitHub scheduling delays. It expects a `repository_dispatch` event of type `trigger-daily-momentum`. 

Alternatively, it can be triggered manually via the **Actions** tab in GitHub using `workflow_dispatch`.
## 🚀 Execution

This pipeline relies on an external cron job service ([cron-job.org](https://cron-job.org)) to bypass standard GitHub scheduling delays. It sends a `repository_dispatch` event of type `trigger-daily-momentum` at 9:00 AM and 1:30 PM EST.
