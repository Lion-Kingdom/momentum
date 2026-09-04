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

spreadsheet_id = "19vJuI1ZE34h1weS8s3_RJEoWz6meVKMliFWvDjm5fc0"


# ==========================================
# 1. PASTE THE CLASSIFIER HERE
# ==========================================
def classify_actionable_setups(df):
    """Audited classifier that logs data state at every single step."""
    print(f"🔍 [AUDIT] Entering classifier. Initial DataFrame shape: {df.shape}")

    if df.empty:
        print("⚠️ [AUDIT] WARNING: DataFrame received by classifier is completely empty!")
        return df

    print(f"🔍 [AUDIT] Available columns in DataFrame: {list(df.columns)}")

    cols_to_convert = ['Open', 'High', 'Low', 'Close', 'Put_Wall_Floor', 'Call_Wall_Ceiling']
    for col in cols_to_convert:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    strategy_tags = []

    for index, row in df.iterrows():
        ticker = row.get('Ticker', 'UNKNOWN')
        momentum = str(row.get('Momentum_Signal', '')).strip()
        regime = str(row.get('Market_Regime', '')).strip()

        print(f"🔍 [AUDIT] Processing Ticker: {ticker} | Momentum: '{momentum}' | Regime: '{regime}'")

        # Baseline tag mapping honoring original scanner output
        if "High-Conviction" in regime or "High-Conviction" in momentum:
            tag = f"🔥 HIGH-CONVICTION OUTLIER: {regime if 'High-Conviction' in regime else momentum}"
        elif momentum and momentum != 'nan' and momentum != '':
            tag = f"QUANT SIGNAL: {momentum}"
        elif regime and regime != 'nan' and regime != '':
            tag = f"QUANT SIGNAL: {regime}"
        else:
            tag = "MOMENTUM PLAY"

        strategy_tags.append(tag)

    df['Actionable_Strategy'] = strategy_tags

    print(f"✅ [AUDIT] Exiting classifier successfully. Final DataFrame shape: {df.shape}")
    return df


def export_gex_to_sheets(gex_dataframe):
    """Pushes the fully enriched dataframe directly to a Google Sheet."""
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = json.loads(os.environ["GCP_SA_KEY"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        
        sheet = client.open_by_key(spreadsheet_id).worksheet("GEX_Report")
        sheet.clear()
        
        # We fill NaNs with empty strings to prevent Google Sheets from crashing
        clean_df = gex_dataframe.fillna("")
        sheet.update([clean_df.columns.values.tolist()] + clean_df.values.tolist())
        
        print("✅ Enriched V2 GEX Master Log successfully pushed to Google Sheets!")
    except Exception as e:
        print(f"❌ Failed to push to Google Sheets: {e}")


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

    # --- 5-DAY ROLLING RETENTION LOGIC (UTC-Safe) ---
    # print("Applying 5-day rolling data retention...")
    # df['Timestamp'] = pd.to_datetime(df['Timestamp'], errors='coerce', utc=True)
    # cutoff = pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=5)
    # df = df[df['Timestamp'] >= cutoff]
    
    # NEW: Classify the setups BEFORE saving the CSV
    print("Tagging strategies for master ledger...")
    df = classify_actionable_setups(df)

    # Save the updated, pruned, and classified dataframe
    df.to_csv(master_csv_path, index=False)
    print("OHLCV data appended, setups classified, and old records pruned successfully.")
    
    # 🚀 NEW: Push the fully enriched dataframe to Google Sheets!
    export_gex_to_sheets(df)
    
    return df


# --- 2. Gemini API Reporting ---
def generate_gemini_report(df):
    """Generates the final HTML report, bypassing AI if the market is truly flat."""
    print(f"🤖 [AUDIT] generate_gemini_report received DataFrame with shape: {df.shape}")
    # THE FLAT MARKET BYPASS (Only triggers if the scanner itself found 0 tickers)
    if df.empty:
        print("❌ [AUDIT] TRAP TRIGGERED: df.empty evaluated to True. Bypassing API.")
        return """
        <div style="background-color: #121212; padding: 20px; font-family: Arial, sans-serif;">
            <div style="background-color: #F9F9F9; color: #111111; border-radius: 8px; padding: 24px; box-shadow: 0 4px 12px rgba(0,0,0,0.3);">
                <h2 style="text-align: center;">🎯 THE PRECISION TRADER: DAILY ACTION PLAN</h2>
                <p>The quantitative scanning engine has executed a complete sweep across options telemetry and price action channels. Current market conditions exhibit zero actionable gamma anomalies or options spread alignments.</p>
                <h3 style="color: #111111; border-bottom: 2px solid #D4AF37; padding-bottom: 5px;">🔥 HIGH-CONVICTION SETUPS</h3>
                <p style="font-weight: bold; color: #D32F2F;">The engine is flat today. Cash is a position.</p>
                <p>Capital preservation remains paramount as we hold cash reserves until high-probability triggers align.</p>
            </div>
        </div>
        """
    print("🚀 [AUDIT] Dataframe has rows! Passing data to Gemini API...")
    report_data = df.to_csv(index=False)
    # IF WE HAVE SETUPS, PREP THE DATA FOR GEMINI
    report_data = df.to_csv(index=False)

    # Load Telemetry for the Breadth Summary
    summary_telemetry = ""
    candidate_paths = [
        "momentum_summary.txt",
        "momentum_suite/momentum_summary.txt",
        os.path.join(os.path.dirname(__file__), "momentum_summary.txt")
    ]
    for path in candidate_paths:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                summary_telemetry = f.read().strip()
            break
            
        # 4. THE V2 SIMPLIFIED AI PROMPT
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
    """

    print("Generating Gemini Deep Dive Report from Classified Data...")
    
    # 5. CALL THE API
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model='gemini-3.6-flash',
                contents=prompt,
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
                    return f"<h2>Error generating AI report: API unavailable.</h2>"
            else:
                print(f"Error calling Gemini: {e}")
                return f"<h2>Error generating AI report: {e}</h2>"
    

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
