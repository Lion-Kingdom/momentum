import pandas as pd
import yfinance as yf
from google import genai
import smtplib
import os
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime


# --- 1. yfinance OHLCV Extraction & Data Pruning ---
def append_ohlcv_data(master_csv_path="unified_gex_momentum_master_log.csv"):
    """Reads the master log, fetches latest OHLCV data, and enforces a 5-day retention policy."""
    print("Fetching OHLCV data...")
    df = pd.read_csv(master_csv_path)
    
    # Initialize new columns for full OHLCV
    for col in ['Open', 'High', 'Low', 'Close']:
        if col not in df.columns:
            df[col] = 0.0
    if 'Volume' not in df.columns:
        df['Volume'] = 0

    for index, row in df.iterrows():
        ticker = row['Ticker']
        # Handle index tickers for yfinance (including XSP)
        yf_ticker = f"^{ticker}" if ticker in ["SPX", "XSP", "NDX", "RUT", "VIX"] \
            and not ticker.startswith("^") else ticker
        
        try:
            stock = yf.Ticker(yf_ticker)
            # Pull 5 days of history to ensure we get a clean latest candle
            hist = stock.history(period="5d")
            if not hist.empty:
                df.at[index, 'Open'] = round(hist['Open'].iloc[-1], 2)
                df.at[index, 'High'] = round(hist['High'].iloc[-1], 2)
                df.at[index, 'Low'] = round(hist['Low'].iloc[-1], 2)
                df.at[index, 'Close'] = round(hist['Close'].iloc[-1], 2)
                df.at[index, 'Volume'] = int(hist['Volume'].iloc[-1])
        except Exception as e:
            print(f"Error fetching OHLCV for {ticker}: {e}")

    # --- 5-DAY ROLLING RETENTION LOGIC ---
    print("Applying 5-day rolling data retention...")
    df['Timestamp'] = pd.to_datetime(df['Timestamp'])
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=5)
    df = df[df['Timestamp'] >= cutoff]
    
    # Save the updated and pruned dataframe
    df.to_csv(master_csv_path, index=False)
    print("OHLCV data appended and old records pruned successfully.")
    return df


# --- 2. Gemini API Reporting ---
def generate_gemini_report(df):
    """Passes filtered Gamma data and raw momentum telemetry to Gemini."""
    print("Generating Gemini Deep Dive Report...")

    # 1. Read the summary text file if it exists
    summary_telemetry = ""
    try:
        with open("momentum_summary.txt", "r") as f:
            summary_telemetry = f.read()
    except Exception as e:
        print(f"Could not read momentum_summary.txt: {e}")

    # 2. Filter DF across Signal, Regime, Strategy, or pull top active rows
    filtered_df = df[
        df['Momentum_Signal'].astype(str).str.contains('Gamma Squeeze|Breakout', case=False, na=False) |
        df['Market_Regime'].astype(str).str.contains('Negative Gamma', case=False, na=False) |
        df['Confirmed_Strategy'].astype(str).str.contains('Squeeze|Spread|Condor', case=False, na=False)
    ]
    
    # If the filter yields no rows, fall back to the last 20 rows rather than passing nothing
    if filtered_df.empty:
        filtered_df = df.tail(20)

    report_data = filtered_df.to_csv(index=False)
    
    print(f"DEBUG: summary_telemetry length: {len(summary_telemetry)}")

    prompt = f"""
    You are the lead quantitative trading analyst for 'The Precision Trader'. 
    Evaluate the provided input telemetry and CSV market data to generate our daily intelligence report.
    Output strictly raw HTML code without markdown fences (no ```html blocks).

    ---
    ### INPUT TELEMETRY & DATA:
    {summary_telemetry}

    {report_data}
    ---

    ### OPERATIONAL CONTRACT & RULES:
    1. SPREAD CANDIDATE MANDATE:
       Extract every ticker identified under 'TOP OPTIONS SPREAD STRATEGY CANDIDATES' in the telemetry (e.g., HOOD, IBKR, LABU). 
       You MUST detail their specific setups:
       - Bull Call Spreads (Debit / Momentum)
       - Bull Put Spreads (Credit / Support)
       - Bear Put Spreads (Debit / Flush)
       - Any Iron Condor / Neutral Range structures

    2. ANTI-FLAT ENFORCEMENT:
       Actionable spread setups and active breadth metrics exist in the data above. 
       You are STRICTLY FORBIDDEN from outputting:
       - "The engine is flat today..."
       - "zero actionable gamma anomalies"
       - "No options structures qualified for execution today"
       - "None (Engine is flat)"

    3. HIGH-CONVICTION ADAPTATION:
       If pure directional 'Negative Gamma' squeeze signals are absent, use the leading spread setups (HOOD, IBKR, LABU) to populate the High-Conviction table and explain why their momentum/structure qualifies them for tactical execution.

    ### HTML DESIGN & STYLING SPECIFICATIONS:
    - Container: Outer container with dark gray background (#121212) and 20px padding.
    - Card: Inner wrapper ("Trade Card") with off-white/light gray background (#F9F9F9), dark text (#111111), border-radius of 8px, padding of 24px, and a subtle box shadow.
    - Typography: Clean sans-serif font family (Arial, Helvetica, sans-serif) with high-contrast text.
    - Tables: Clean HTML <table> with full width, dark slate header (#1E1E1E) with white bold text, alternating row shading (#FFFFFF and #F0F0F0), and cell padding of 10px.
    - Accents: Use bold text and gold (#D4AF37) or deep green (#2E7D32) for key strike levels, entry triggers, and target zones.

    ### REPORT STRUCTURE (Follow this exact order):

    1. 🎯 THE PRECISION TRADER: DAILY ACTION PLAN
     - Center-aligned H2 header.
     - 2-3 sentence executive market summary derived from the phase breadth and volatility alert count in the telemetry.

    2. 🔥 HIGH-CONVICTION SETUPS
     - H3 header.
     - HTML table containing the primary candidates (HOOD, IBKR, LABU) with columns:
       Ticker | Strategy / Focus | Spot / Close | Key Pivot / Level | Technical Context

    3. 🛠️ THE OPTIONS PLAYBOOK
     - H3 header.
     - For EACH extracted ticker, provide an individual visual block/card detailing:
       * Strategy Type (e.g., Bull  Call Debit Spread, Bull Put Credit Spread)
       * Setup Trigger (Price, RSI, 1M/5D momentum stats from the data)
       * Execution Plan: Define a realistic 2-strike bracket (Long Strike / Short Strike) and target expiry window (1-4 DTE or weekly).

    4. 🛡️ PRECISION RISK MANAGEMENT & EXECUTION RULES
     - H3 header.
     - Standard execution guardrails:
       * Position Sizing: Max 10% portfolio capital per trade setup.
       * Time Horizon: 1-4 days tactical execution window.
       * Profit Target: Systematic scale-out at 50% - 70% return on risk/premium.
       q* Stop Loss Trigger: Hard deck cutoff at 15% - 30% drawdown.

    5. 📉 CHARTING WATCHLIST
     - H3 header.
     - Bulleted or comma-separated list of the active tickers with their critical structural ceiling and floor levels.
    """

    # --- BULLETPROOF API CALL WITH AUTO-RETRY ---
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model='gemini-3.6-flash',
                contents=prompt
            )
            # Clean up any stray "None" or rogue tags at the top
            clean_html = response.text.strip()
            if clean_html.startswith("None"):
                clean_html = clean_html[4:].strip()
            return clean_html
        except Exception as e:
            error_msg = str(e)
            if "503" in error_msg or "429" in error_msg or "UNAVAILABLE" in error_msg:
                if attempt < max_retries - 1:
                    print(f"⚠️ Google API busy. Retrying in 30 seconds... (Attempt {attempt + 1}/{max_retries})")
                    time.sleep(30)
                else:
                    print("❌ Max retries reached. Google AI servers are currently down.")
                    raise e
            else:
                raise e


# --- 3. Email Delivery ---
def send_email_report(report_content):
    """Sends the generated report via email using smtplib."""
    print("Dispatching email report...")
    sender = os.getenv("EMAIL_USER")
    pwd = os.getenv("EMAIL_PASS")
    
    # Restrict to safe solo testing for now
    recipients = [sender, "new_being@hotmail.com"] 
    
    if not sender or not pwd:
        print("⚠️ Email secrets not configured. Skipping email dispatch.")
        return

    now_str = datetime.now().strftime("%b %d, %Y")
    
    for recipient in recipients:
        msg = MIMEMultipart()
        msg['From'] = f'"Leon EL Cee" <{sender}>'
        msg['To'] = recipient
        msg['Subject'] = f"🧠 AI Deep Dive Market Report: Gamma Regimes ({now_str})"
        msg.attach(MIMEText(report_content, 'html'))
        
        try:
            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.starttls()
            server.login(sender, pwd)
            server.sendmail(sender, recipient, msg.as_string())
            server.quit()
            print(f"✅ AI Report successfully delivered to {recipient}!")
        except Exception as e:
            print(f"❌ Email failed for {recipient}: {e}")


if __name__ == "__main__":
    # 1. Update the CSV with yfinance data (including XSP)
    updated_df = append_ohlcv_data("momentum_suite/unified_gex_momentum_master_log.csv")

    # 2. Generate the report with Gemini
    ai_report = generate_gemini_report(updated_df)

    # 3. Read the FULL Momentum Stats Report generated by the first script
    try:
        with open("momentum_suite/momentum_summary.txt", "r", encoding="utf-8") as f:
            momentum_stats = f.read()
    except FileNotFoundError:
        momentum_stats = "(Momentum detailed stats unavailable for this run)"

    # 4. Merge them inside a centered master container so everything stays balanced
    final_master_report = f"""
    <div style="background-color: #121212; padding: 20px; width: 100%;">
        <div style="max-width: 650px; margin: 0 auto;">
        {ai_report}
            <br><hr style="border: 1px solid #333;"><br>
            <div style="background-color: #1a1a1a; color: #00ff66; padding: 15px; border-radius: 8px; overflow-x: auto;">
                <h3 style="color: #ffffff; margin-top: 0;">📊 Detailed Momentum & Spread Telemetry</h3>
                <pre style="font-family: 'Courier New', monospace; font-size: 12px; white-space: pre-wrap;">{momentum_stats}</pre>
            </div>
        </div>
    </div>
    """

    # 5. Send the ONE final combined email
    send_email_report(final_master_report)
