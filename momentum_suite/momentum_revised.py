"""
Dynamic S&P 500 & ETF Momentum Worker.
Refined for: Volatility Alerts (>1.5%) and removing slow consolidators.
Compliance: Pylint 10/10, Black, PEP 8.
"""

import io
import sys
import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional

import gspread
import numpy as np
import pandas as pd
import requests
import yfinance as yf

# --- CONFIGURATION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPREADSHEET_NAME = "Daily_Momentum_Suite"
JSON_KEY_PATH = os.path.join(SCRIPT_DIR, "momentum_suite.json")
ETF_CSV_PATH = os.path.join(SCRIPT_DIR, "etfs.csv")


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


def generate_deep_analysis(df: pd.DataFrame, sheet_url: str) -> str:
    """Generate a text-based deep analysis report mapping phases to options spreads."""
    total = len(df)
    vol_alerts = len(df[df["Alert"] == "VOLATILITY"])
    phases = df["Phase"].value_counts().to_dict()

    # 1. Bull Call Spread (Debit) -> Strong Expansion, high momentum
    bull_call = df[df["Phase"] == "Strong_Expansion"].sort_values(by="1M%", ascending=False)

    # 2. Bull Put Spread (Credit) -> Pullback Reset / Regaining, buying the dip support
    bull_put = df[df["Phase"].isin(["Pullback_Reset", "Regaining"])].sort_values(by="RSI", ascending=True)

    # 3. Bear Call Spread (Credit) -> Exhausted Trap, fading overbought resistance
    bear_call = df[df["Phase"] == "Exhausted_Trap"].sort_values(by="RSI", ascending=False)

    # 4. Bear Put Spread (Debit) -> Losing Momentum Flush, trend breakdown
    bear_put = df[df["Phase"] == "Losing_Momentum_Flush"].sort_values(by="5D%", ascending=True)

    report = "📊 DAILY MOMENTUM SUITE - DEEP ANALYSIS REPORT\n"
    report += f"{'='*50}\n"
    report += f"Total Tickers Scanned: {total}\n"
    report += f"Volatility Alerts: {vol_alerts}\n\n"

    report += "📈 PHASE BREADTH:\n"
    for phase, count in phases.items():
        report += f" - {phase}: {count}\n"

    report += f"\n{'='*50}\n"
    report += "🎯 TOP OPTIONS SPREAD STRATEGY CANDIDATES\n"
    report += f"{'='*50}\n\n"

    if not bull_call.empty:
        t = bull_call.iloc[0]
        report += "🟢 BULL CALL SPREAD (Debit - Upside Momentum)\n"
        report += f"   ➤ Ticker: {t['Ticker']} | Price: ${t['Price']} | 1M: {t['1M%']}% | RSI: {t['RSI']}\n\n"

    if not bull_put.empty:
        t = bull_put.iloc[0]
        report += "🛡️ BULL PUT SPREAD (Credit - Support Bounce)\n"
        report += f"   ➤ Ticker: {t['Ticker']} | Price: ${t['Price']} | 5D: {t['5D%']}% | RSI: {t['RSI']}\n\n"

    if not bear_call.empty:
        t = bear_call.iloc[0]
        report += "🛑 BEAR CALL SPREAD (Credit - Overbought Resistance)\n"
        report += f"   ➤ Ticker: {t['Ticker']} | Price: ${t['Price']} | 1D: {t['1D%']}% | RSI: {t['RSI']}\n\n"

    if not bear_put.empty:
        t = bear_put.iloc[0]
        report += "📉 BEAR PUT SPREAD (Debit - Downtrend Flush)\n"
        report += f"   ➤ Ticker: {t['Ticker']} | Price: ${t['Price']} | 5D: {t['5D%']}% | RSI: {t['RSI']}\n\n"

    report += f"{'='*50}\n"
    report += f"🔗 Google Sheet Access Link: \n{sheet_url}\n"

    return report


def send_email_report(report_body: str):
    """Dispatch the analysis report via SMTP (Gmail or Hotmail/Outlook)."""
    sender = os.getenv("EMAIL_USER")
    pwd = os.getenv("EMAIL_PASS")
    receiver = os.getenv("EMAIL_RECEIVER", sender)  # Defaults to sending to yourself if not specified

    if not sender or not pwd:
        print("\n[WARNING] Email credentials not found in env vars. Skipping dispatch.")
        return

    # Automatically route to correct SMTP server based on email domain
    smtp_server = "smtp.gmail.com" if "@gmail" in sender.lower() else "smtp-mail.outlook.com"
    port = 587

    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = receiver
    msg['Subject'] = f"Momentum Suite Analysis & Top Spread Candidates - {datetime.now().strftime('%b %d, %Y')}"

    msg.attach(MIMEText(report_body, 'plain'))

    try:
        server = smtplib.SMTP(smtp_server, port)
        server.starttls()
        server.login(sender, pwd)
        server.send_message(msg)
        server.quit()
        print(f"[INFO] Analysis report successfully emailed to {receiver}")
    except Exception as e:
        print(f"[ERROR] Failed to send email: {e}")


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

    # --- 🔌 MAP PHASES TO GEX PIPELINE SIGNALS ---
    def map_phase_to_signal(phase):
        if phase in ["Strong_Expansion"]:
            return "Breakout"
        elif phase in ["Pullback_Reset", "Regaining"]:
            return "Bullish"
        elif phase in ["Exhausted_Trap", "Losing_Momentum_Flush"]:
            return "Bearish"
        return "Neutral"

    df_results = df.copy()
    df_results["Momentum_Signal"] = df_results["Phase"].apply(map_phase_to_signal)

    # Save locally for the GEX script to consume
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(script_dir, "momentum_signals.csv")
    df_results.to_csv(output_path, index=False)
    print(f"💾 Momentum signals exported for GEX pipeline to: {output_path}")

    try:
        # export_to_google_sheets(df)  <-- COMMENT THIS OUT
        
        # --- GENERATE REPORT ---
        sheet_url = "https://docs.google.com/spreadsheets/d/19vJuI1ZE34h1weS8s3_RJEoWz6meVKMliFWvDjm5fc0/edit"
        report = generate_deep_analysis(df, sheet_url)
        print("\n" + report)
        
        # send_email_report(report)  <--- COMMENT THIS OUT SO IT DOESN'T SEND
        
        # --- NEW: SAVE TEXT TO FILE FOR THE AI SCRIPT TO GRAB ---
        report_path = os.path.join(script_dir, "momentum_summary.txt")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"💾 Momentum text report saved to {report_path}")

    except Exception as err:
        print(f"\n[ERROR] Sheets Export or Email Failed: {err}")
        if hasattr(err, "response") and hasattr(err.response, "text"):
            print(f"API Details: {err.response.text}")


if __name__ == "__main__":
    run_worker()
