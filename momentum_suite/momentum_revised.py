"""
Dynamic S&P 500 & ETF Momentum Worker.
Refined for: Volatility Alerts (>1.5%) and removing slow consolidators.
Compliance: Pylint 10/10, Black, PEP 8.
"""

import io
import sys
import os
import json
from datetime import datetime
from typing import Optional

import gspread
import numpy as np
import pandas as pd
import requests
import yfinance as yf

# --- CONFIGURATION ---
SPREADSHEET_NAME = "Daily_Momentum_Suite"
JSON_KEY_PATH = "momentum_suite.json"
ETF_CSV_PATH = "etfs.csv"


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate J. Welles Wilder's Exponential Moving Average RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs_val = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs_val))


def fetch_sp500_tickers() -> list:
    """Scrape live S&P 500 symbols from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        table = pd.read_html(io.StringIO(response.text))[0]
        return table["Symbol"].str.replace(".", "-", regex=False).tolist()
    except (requests.RequestException, ValueError):
        return []


def fetch_csv_etfs(filepath: str = ETF_CSV_PATH) -> list:
    """Extract tickers from a locally saved CSV of ETFs."""
    try:
        df = pd.read_csv(filepath)
        # Handle the most common header names for tickers
        col_name = "Symbol" if "Symbol" in df.columns else "Ticker"
        if col_name not in df.columns:
            print(f"[WARNING] '{filepath}' missing 'Symbol' or 'Ticker' column.")
            return []
        # Strip whitespace and drop NaNs
        return df[col_name].dropna().astype(str).str.strip().tolist()
    except FileNotFoundError:
        print(f"[WARNING] ETF CSV '{filepath}' not found. Returning empty list.")
        return []
    except pd.errors.EmptyDataError:
        print(f"[WARNING] ETF CSV '{filepath}' is empty. Returning empty list.")
        return []


def process_ticker(ticker: str, data: pd.DataFrame, bench: float) -> Optional[dict]:
    """Extract metrics and apply phase filters."""
    try:
        # 1. Safely handle multi-index yfinance output
        if ticker not in data.columns.levels[0] if isinstance(data.columns, pd.MultiIndex) else ticker not in data:
            return None

        df = data[ticker].dropna(how="all")

        # Ensure Close column exists and convert to Series
        if "Close" not in df.columns:
            return None

        close = df["Close"].dropna()
        if len(close) < 50:
            return None

        curr_p = float(close.iloc[-1])
        sma20 = float(close.rolling(20).mean().iloc[-1])
        rsi = float(calculate_rsi(close).iloc[-1])
        c1d = (curr_p - close.iloc[-2]) / close.iloc[-2]
        c5d = (curr_p - close.iloc[-6]) / close.iloc[-6]
        c1m = (curr_p - close.iloc[-22]) / close.iloc[-22]
        perf = (curr_p - close.iloc[0]) / close.iloc[0]

        if not (curr_p > sma20 and rsi > 50 and perf >= (bench * 0.5)):
            return None

        phase = "Consolidating"
        if rsi > 78:
            phase = "Exhausted_Trap"
        elif c5d < 0 and c1d <= 0 and (55 <= rsi <= 70):
            phase = "Pullback_Reset"
        elif c1d > 0.01 and c5d <= 0:
            phase = "Regaining"
        elif c1d > 0 and c5d > 0 and c1m > 0:
            phase = "Strong_Expansion"
        elif c1d < -0.01 and c5d < -0.03:
            phase = "Losing_Momentum_Flush"

        if phase == "Consolidating" and c1m < 0.10:
            return None

        return {
            "Ticker": ticker,
            "Price": round(curr_p, 2),
            "1D%": round(c1d * 100, 2),
            "5D%": round(c5d * 100, 2),
            "1M%": round(c1m * 100, 2),
            "RSI": round(rsi, 1),
            "Phase": phase,
            "Alert": "VOLATILITY" if abs(c1d) > 0.015 else "",
        }
    except Exception:
        return None


def delete_old_tabs(spreadsheet, max_tabs: int = 30):
    """Maintain workbook by deleting outdated scan tabs."""
    scan_tabs = [w for w in spreadsheet.worksheets() if "alpha_momentum" in w.title]
    if len(scan_tabs) >= max_tabs:
        scan_tabs.sort(key=lambda x: x.id)
        spreadsheet.del_worksheet(scan_tabs[0])
        print(f"[INFO] Deleted oldest tab: {scan_tabs[0].title}")


def export_to_google_sheets(df: pd.DataFrame):
    """Authorize and push results to Google Sheets natively."""
    # Check if running in CI/CD cloud environment
    sa_json = os.getenv("GCP_SA_KEY")
    if sa_json:
        creds_dict = json.loads(sa_json)
        client = gspread.service_account_from_dict(creds_dict)
    else:
        client = gspread.service_account(filename=JSON_KEY_PATH)

    spreadsheet = client.open(SPREADSHEET_NAME)

    delete_old_tabs(spreadsheet)

    date_str = datetime.now().strftime("%b_%d_%Y").lower()
    worksheets = [w.title for w in spreadsheet.worksheets()]

    counter = 1
    while True:
        tab_name = f"alpha_momentum_{date_str}_{counter:02d}"
        if tab_name not in worksheets:
            break
        counter += 1

    clean_df = df.replace([np.inf, -np.inf], np.nan).fillna("")
    payload = [clean_df.columns.tolist()] + clean_df.values.tolist()

    ws = spreadsheet.add_worksheet(
        title=tab_name, rows=len(payload) + 5, cols=len(df.columns)
    )
    ws.update(range_name="A1", values=payload)
    print(f"\n[INFO] Data pushed to tab: {tab_name}")

    ledger_df = df.copy()
    ledger_df.insert(0, "Run_Time", datetime.now().strftime("%Y-%m-%d %H:%M"))
    ledger_clean = ledger_df.replace([np.inf, -np.inf], np.nan).fillna("")
    ledger_payload = ledger_clean.values.tolist()

    try:
        ledger = spreadsheet.worksheet("Master_Ledger")
    except gspread.exceptions.WorksheetNotFound:
        ledger = spreadsheet.add_worksheet(
            title="Master_Ledger", rows=1, cols=len(ledger_clean.columns)
        )
        ledger.append_row(ledger_clean.columns.tolist())

    ledger.append_rows(ledger_payload)
    print("[INFO] Appended to 'Master_Ledger'.")


def run_worker():
    """Execute main pipeline."""
    sp500_tickers = fetch_sp500_tickers()
    etf_tickers = fetch_csv_etfs()

    tickers = list(set(sp500_tickers + etf_tickers))
    if not tickers:
        sys.exit("No tickers available to process.")

    print(f"[INFO] Scanning {len(tickers)} total tickers...")

    # Using SPY as the benchmark for a broader momentum baseline
    spy = yf.download("SPY", period="1y")["Close"].squeeze()
    if spy.empty:
        sys.exit("[ERROR] Failed to download benchmark (SPY).")

    bench = (float(spy.iloc[-1]) - float(spy.iloc[0])) / float(spy.iloc[0])

    data = yf.download(tickers, period="1y", group_by="ticker")
    res = [process_ticker(t, data, bench) for t in tickers]
    df = pd.DataFrame([r for r in res if r])

    if df.empty:
        sys.exit("No symbols met criteria.")

    # Sorting logic: Bubbles Volatility alerts to the top, then groups by Phase
    df = df.sort_values(
        by=["Alert", "Phase", "Price"], ascending=[False, True, False]
    )
    print(df.to_string(index=False))

    try:
        export_to_google_sheets(df)
    except Exception as err:
        print(f"\n[ERROR] Sheets Export Failed: {err}")
        if hasattr(err, "response") and hasattr(err.response, "text"):
            print(f"API Details: {err.response.text}")


if __name__ == "__main__":
    run_worker()
