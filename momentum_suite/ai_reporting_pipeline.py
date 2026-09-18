"""
AI Reporting Pipeline for Momentum and Gamma Screener.
Processes the GEX main log, queries Gemini, dispatches HTML email,
and includes a mobile-friendly accordion fallback telemetry board.
"""

import json
import os
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import gspread
import pandas as pd
import yfinance as yf
from google import genai
from oauth2client.service_account import ServiceAccountCredentials

SPREADSHEET_ID = "19vJuI1ZE34h1weS8s3_RJEoWz6meVKMliFWvDjm5fc0"


def classify_actionable_setups(df):
    """Audited classifier that logs data state at every single step."""
    print(f"🔍 [AUDIT] Entering classifier. Initial DataFrame shape: {df.shape}")

    if df.empty:
        print("⚠️ [AUDIT] WARNING: DataFrame received by classifier is empty!")
        return df

    print(f"🔍 [AUDIT] Available columns in DataFrame: {list(df.columns)}")

    cols_to_convert = [
        "Open",
        "High",
        "Low",
        "Close",
        "Put_Wall_Floor",
        "Call_Wall_Ceiling",
    ]
    for col in cols_to_convert:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    strategy_tags = []

    for _, row in df.iterrows():
        ticker = row.get("Ticker", "UNKNOWN")
        momentum = str(row.get("Momentum_Signal", "")).strip()
        regime = str(row.get("Market_Regime", "")).strip()

        print(f"🔍 [AUDIT] Processing {ticker} | Momentum: '{momentum}' | Regime: '{regime}'")  # noqa

        if "High-Conviction" in regime or "High-Conviction" in momentum:
            tag = f"🔥 HIGH-CONVICTION OUTLIER: {regime if 'High-Conviction' in regime else momentum}"  # noqa
        elif momentum and momentum != "nan" and momentum != "":
            tag = f"QUANT SIGNAL: {momentum}"
        elif regime and regime != "nan" and regime != "":
            tag = f"QUANT SIGNAL: {regime}"
        else:
            tag = "MOMENTUM PLAY"

        strategy_tags.append(tag)

    df["Actionable_Strategy"] = strategy_tags

    print(f"✅ [AUDIT] Exiting classifier successfully. Final shape: {df.shape}")
    return df


def export_gex_to_sheets(gex_dataframe):
    """Pushes the fully enriched dataframe directly to a Google Sheet."""
    try:
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds_dict = json.loads(os.environ["GCP_SA_KEY"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)

        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("GEX_Report")
        sheet.clear()

        clean_df = gex_dataframe.fillna("")
        sheet.update([clean_df.columns.values.tolist()] + clean_df.values.tolist())

        print("✅ Enriched V2 GEX Main Log successfully pushed to Google Sheets!")
    except Exception as e:  # pylint: disable=broad-except
        print(f"❌ Failed to push to Google Sheets: {e}")


def append_ohlcv_data(main_csv_path="unified_gex_momentum_main_log.csv"):
    """Reads the main log, fetches latest OHLCV data, and classifies setups."""
    print("Fetching OHLCV data...")

    try:
        df = pd.read_csv(main_csv_path)
    except FileNotFoundError:
        print(f"⚠️ [AUDIT] File {main_csv_path} not found. Triggering flat market mode.")  # noqa
        df = pd.DataFrame()

    if df.empty:
        return df

    for col in ["Open", "High", "Low", "Close"]:
        if col not in df.columns:
            df[col] = 0.0
    if "Volume" not in df.columns:
        df["Volume"] = 0

    for index, row in df.iterrows():
        ticker = row["Ticker"]
        yf_ticker = (
            f"^{ticker}"
            if ticker in ["SPX", "XSP", "NDX", "RUT", "VIX"]
            and not ticker.startswith("^")
            else ticker
        )

        try:
            stock = yf.Ticker(yf_ticker)
            hist = stock.history(period="5d")
            if not hist.empty:
                df.at[index, "Open"] = round(hist["Open"].iloc[-1], 2)
                df.at[index, "High"] = round(hist["High"].iloc[-1], 2)
                df.at[index, "Low"] = round(hist["Low"].iloc[-1], 2)
                df.at[index, "Close"] = round(hist["Close"].iloc[-1], 2)
                df.at[index, "Volume"] = int(hist["Volume"].iloc[-1])
        except Exception as e:  # pylint: disable=broad-except
            print(f"Error fetching OHLCV for {ticker}: {e}")

    print("Tagging strategies for main ledger...")
    df = classify_actionable_setups(df)

    df.to_csv(main_csv_path, index=False)
    print("OHLCV data appended and setups classified successfully.")

    export_gex_to_sheets(df)

    return df


def generate_raw_telemetry_board(df):
    """Builds a mobile-friendly accordion dropdown telemetry board grouped by Phase."""
    if df.empty:
        return "<p style='color: #aaa;'>No active GEX telemetry records found for this session.</p>"

    phases = [
        "Strong_Expansion",
        "Consolidating",
        "Pullback_Reset",
        "Regaining",
        "Exhausted_Trap",
    ]

    html_sections = []
    html_sections.append(
        """
    <div style="background-color: #121212; color: #fff; padding: 15px; border-radius: 8px; font-family: Arial, sans-serif;">
        <h3 style="color: #00ff66; margin-top: 0; border-bottom: 2px solid #333; padding-bottom: 8px;">
          🛰️ Community AI Fallback Telemetry Dashboard
        </h3>
        <p style="color: #aaa; font-size: 11px; margin-bottom: 15px;">
          Interactive phase breakdown. Tap any phase below to expand tickers, view raw pricing metrics, and inspect GEX option walls.
        </p>
    """  # noqa
    )

    for phase in phases:
        phase_df = (
            df[df["Phase"].str.strip() == phase]
            if "Phase" in df.columns
            else pd.DataFrame()
        )
        count = len(phase_df)

        if count == 0:
            continue

        accent_color = (
            "#00ff66"
            if phase in ["Strong_Expansion", "Regaining"]
            else ("#ffaa00" if phase == "Consolidating" else "#ff4444")
        )

        html_sections.append(
            f"""
        <details style="background: #1a1a1a; border: 1px solid #333; border-radius: 8px; margin-bottom: 12px; padding: 12px; box-shadow: 0 2px 5px rgba(0,0,0,0.2);">
            <summary style="color: {accent_color}; font-weight: bold; cursor: pointer; font-size: 13px; outline: none;">
                📈 {phase.replace('_', ' ')} ({count} Tickers) — Click to Expand
            </summary>
            <div style="margin-top: 10px; border-top: 1px solid #333; padding-top: 10px; overflow-x: auto;">
        """  # noqa
        )

        for _, row in phase_df.iterrows():
            ticker = row.get("Ticker", "UNKNOWN")
            mom_signal = row.get("Momentum_Signal", "Neutral")
            price = row.get("Price", 0.0)
            open_p = row.get("Open", 0.0)
            high = row.get("High", 0.0)
            low = row.get("Low", 0.0)
            vol = row.get("Volume", 0)
            d1 = row.get("1D%", 0.0)
            d5 = row.get("5D%", 0.0)
            m1 = row.get("1M%", 0.0)
            rsi = row.get("RSI", 0.0)
            alert = row.get("Alert", "N/A")

            html_sections.append(
                f"""
                <div style="background: #222; border-left: 3px solid {accent_color}; padding: 10px; margin-bottom: 10px; border-radius: 4px;">
                    <div style="font-family: 'Courier New', monospace; font-size: 11px; color: #00ff66; font-weight: bold; margin-bottom: 4px;">
                        🔄 {ticker} | Signal: {mom_signal} | Alert: {alert}
                    </div>
                    <div style="font-family: 'Courier New', monospace; font-size: 10px; color: #ccc; margin-bottom: 6px;">
                      Price: ${price:,.2f} | Open: ${open_p:,.2f} | High: ${high:,.2f} | Low: ${low:,.2f} | Vol: {int(vol):,} | 1D: {d1}% | 5D: {d5}% | 1M: {m1}% | RSI: {rsi}
                    </div>
            """  # noqa
            )

            ticker_gex = df[df["Ticker"] == ticker]
            if not ticker_gex.empty and "Target_Expiry" in ticker_gex.columns:
                for _, gex_row in ticker_gex.iterrows():
                    timeframe = gex_row.get("Timeframe", "~7 DTE")
                    expiry = gex_row.get("Target_Expiry", "N/A")
                    dte = gex_row.get("Actual_DTE", 0)
                    call_wall = gex_row.get("Call_Wall_Ceiling", 0.0)
                    put_wall = gex_row.get("Put_Wall_Floor", 0.0)
                    flip = gex_row.get("Gamma_Flip", 0.0)
                    regime = gex_row.get("Market_Regime", "POSITIVE GAMMA")
                    strategy = gex_row.get("Confirmed_Strategy", "Stand Aside")
                    targets = gex_row.get("Target_Strikes", "N/A")

                    html_sections.append(
                        f"""
                    <div style="font-family: 'Courier New', monospace; font-size: 10px; color: #aaa; margin-left: 10px; margin-top: 4px; border-left: 1px dotted #555; padding-left: 6px;">
                      ⏱️ {timeframe} -> Expiry: {expiry} ({dte} DTE)<br>
                      📊 Walls -> Call: ${call_wall:,.2f} | Put: ${put_wall:,.2f} | Flip: ${flip:,.2f}<br>
                      📈 Regime: {regime} | Strategy: <b>{strategy}</b> ({targets})
                    </div>
                    """  # noqa
                    )

            html_sections.append("</div>")

        html_sections.append("</div></details>")

    html_sections.append("</div>")
    return "".join(html_sections)


def generate_gemini_report(df):
    """Generates the final HTML report, bypassing AI if the market is truly flat."""
    print(f"🤖 [AUDIT] generate_gemini_report received DataFrame shape: {df.shape}")

    if df.empty:
        print("❌ [AUDIT] TRAP TRIGGERED: df.empty evaluated to True. Bypassing API.")
        return """
        <div style="background-color: #121212; padding: 20px; font-family: Arial, sans-serif;">
            <div style="background-color: #F9F9F9; color: #111111; border-radius: 8px; padding: 24px; box-shadow: 0 4px 12px rgba(0,0,0,0.3);">
                <h2 style="text-align: center;">🎯 THE PRECISION TRADER: TODAY'S MARKET FLOW & TRADE SETUPS</h2>
                <p>The quantitative scanning engine has executed a complete sweep across options telemetry and price action channels. Current market conditions exhibit zero actionable gamma anomalies or options spread alignments.</p>
                <h3 style="color: #111111; border-bottom: 2px solid #D4AF37; padding-bottom: 5px;">🔥 HIGH-CONVICTION SETUPS</h3>
                <p style="font-weight: bold; color: #D32F2F;">The engine is flat today. Cash is a position.</p>
                <p>Capital preservation remains paramount as we hold cash reserves until high-probability triggers align.</p>
            </div>
        </div>
        """  # noqa

    print("🚀 [AUDIT] Dataframe has rows! Passing data to Gemini API...")
    report_data = df.to_csv(index=False)

    summary_telemetry = ""
    candidate_paths = [
        "momentum_summary.txt",
        "momentum_suite/momentum_summary.txt",
        os.path.join(os.path.dirname(__file__), "momentum_summary.txt"),
    ]
    for path in candidate_paths:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                summary_telemetry = f.read().strip()
            break

    prompt = f"""
    You are the quantitative trading analyst for 'The Precision Trader'. 
    Take the Pre-Classified Python data below and format it into our daily HTML report.
    
    --- RAW TELEMETRY & BREADTH --- 
    {summary_telemetry}
    
    --- CLASSIFIED OPTIONS STRATEGY DATA ---
    {report_data}
    
    CRITICAL RULES:
    1. NO HALLUCINATIONS: You MUST strictly use the Tickers, Spot Prices, and 'Actionable_Strategy' provided in the CSV data. Do not invent tickers or use default prices.
    2. The 'Actionable_Strategy' column tells you EXACTLY what strategy to assign to each ticker (e.g., '0 DTE BULL BOUNCE (Sell Put Credit Spread)'). DO NOT change the strategy.
    
    HTML DESIGN:
    - Outer wrapper: dark gray (#121212) with 20px padding.
    - Inner card: off-white (#F9F9F9), dark text, 8px border-radius, 24px padding.
    - Tables: Full width, dark header row (#1E1E1E), alternating row shading.

    STRUCTURE:
    1. 🎯 THE PRECISION TRADER: DAILY ACTION PLAN (H2, centered)
       - 2-3 sentence executive summary based on the breadth in the raw telemetry.
    2. 🔥 ACTIONABLE OPTIONS PLAYBOOK (H3)
       - Build a clean HTML table featuring the tickers from the CSV. 
       - Columns: Ticker | Spot Price | Put Wall | Call Wall | Actionable Strategy
    3. 🛠️ TACTICAL EXECUTION CARDS (H3)
       - Create a brief bulleted card for up to 3 of the top setups from the CSV, detailing their specific strike bracket logic based on their Put/Call walls.
    4. 🛡️ RISK MANAGEMENT (H3)
       - Include standard rules: Position Sizing (Max 10%), Time Horizon (0 DTE or 1-4 Days Swing), Profit Target (50-70%), Stop Loss (15-30%).
    """  # noqa

    print("Generating Gemini Deep Dive Report from Classified Data...")
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt,
            )

            clean_html = response.text.strip()
            if clean_html.startswith("None"):
                clean_html = clean_html[4:].strip()
            return clean_html

        except Exception as e:  # pylint: disable=broad-except
            error_msg = str(e)
            if "503" in error_msg or "429" in error_msg or "UNAVAILABLE" in error_msg:
                if attempt < max_retries - 1:
                    print(f"⚠️ Google API busy. Retrying... (Attempt {attempt + 1}/{max_retries})")  # noqa
                    time.sleep(30)
                else:
                    print("❌ Max retries reached. Google AI servers are currently down.")  # noqa
                    return "<h2>Error generating AI report: API unavailable. See raw fallback telemetry below.</h2>"  # noqa
            else:
                print(f"Error calling Gemini: {e}")
                return f"<h2>Error generating AI report: {e}</h2>"


def send_email_report(report_content):
    """Sends the generated HTML report via email using smtplib."""
    print("Dispatching email report...")
    sender = os.getenv("EMAIL_USER")
    pwd = os.getenv("EMAIL_PASS")

    recipients = [
        sender,
        "new_being@hotmail.com",
        "uroberts54@gmail.com",
        "sdimi22@aol.com",
        "lordruckus88@gmail.com",
        "walterljackson@hotmail.com",
        "klovebfly620@hotmail.com"
    ]

    if not sender or not pwd:
        print("⚠️ Email secrets not configured. Skipping email dispatch.")
        return

    now_str = datetime.now().strftime("%b %d, %Y")

    for recipient in recipients:
        msg = MIMEMultipart()
        msg["From"] = f'"Leon EL Cee" <{sender}>'
        msg["To"] = recipient
        msg["Subject"] = (
            f"🎯 The Precision Trader Automation: Today's Market Flow & Trade Setups"
            f" ({now_str})"
        )
        msg.attach(MIMEText(report_content, "html"))

        try:
            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.starttls()
            server.login(sender, pwd)
            server.sendmail(sender, recipient, msg.as_string())
            server.quit()
            print(f"✅ AI Report successfully delivered to {recipient}!")
        except Exception as e:  # pylint: disable=broad-except
            print(f"❌ Email failed for {recipient}: {e}")


if __name__ == "__main__":
    # Check both root and momentum_suite paths so it never misses the file
    target_path = "unified_gex_momentum_main_log.csv"
    if not os.path.exists(target_path) and os.path.exists("momentum_suite/unified_gex_momentum_main_log.csv"):
        target_path = "momentum_suite/unified_gex_momentum_main_log.csv"

    updated_df = append_ohlcv_data(target_path)

    ai_report = generate_gemini_report(updated_df)
    
    # 🟢 NEW: Generate the Raw Community Telemetry Board
    raw_telemetry_text = generate_raw_telemetry_board(updated_df)

    try:
        summary_path = "momentum_summary.txt"
        if not os.path.exists(summary_path) and os.path.exists("momentum_suite/momentum_summary.txt"):
            summary_path = "momentum_suite/momentum_summary.txt"

        with open(summary_path, "r", encoding="utf-8") as f:
            momentum_stats = f.read()
    except FileNotFoundError:
        momentum_stats = "(Momentum detailed stats unavailable for this run)"

    final_main_report = f"""
    <div style="background-color: #121212; padding: 20px; width: 100%; font-family: Arial, sans-serif;">
        <div style="max-width: 650px; margin: 0 auto;">
            {ai_report}
            <br>
            
            {raw_telemetry_text}
            
            <br>
            <div style="background-color: #1a1a1a; border-left: 4px solid #00ff66; border-radius: 6px; padding: 18px; color: #e0e0e0;">
                <h3 style="color: #00ff66; margin-top: 0; font-size: 16px; letter-spacing: 0.5px;">
                    ⚡ QUANT SUITE: MULTI-STRATEGY RADAR & BREADTH
                </h3>
                <p style="font-size: 13px; color: #a0a0a0; margin-bottom: 12px;">
                    Alternative strategy angles (Debit expansion & Overbought fades) from the underlying momentum engine:
                </p>
                <div style="background-color: #111111; padding: 12px; border-radius: 4px; border: 1px solid #2a2a2a;">
                    <pre style="font-family: 'Consolas', 'Courier New', monospace; font-size: 12px; color: #00ff66; margin: 0; white-space: pre-wrap; line-height: 1.5;">{momentum_stats}</pre>
                </div>
            </div>
        </div>
    </div>
    """  # noqa

    send_email_report(final_main_report)
