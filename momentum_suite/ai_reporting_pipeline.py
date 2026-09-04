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
    You are the quantitative trading analyst for 'The Precision Trader'. 
    Evaluate the live input telemetry and CSV market data below.
    You MUST derive all tickers, prices, and metrics strictly from the input data provided. 
    DO NOT hallucinate ticker names, historical prices, or default values.

    ---
    ### RAW INPUT DATA:
    {summary_telemetry}

    {report_data}
    ---

    ### OPERATIONAL CONTRACT & SELECTION HIERARCHY:

    1. STRICT DATA BINDING:
       - Every ticker, spot price, RSI, and percentage change used in the report MUST match the input data exactly.
       - If a ticker is listed at $124.72, use $124.72. Construct all option strike brackets within ±2% to 5% of that actual spot price.

    2. TIERED SETUP SELECTION:
       - PRIMARY (High-Conviction Directional): If any ticker has an explicit 'Negative Gamma' regime or 'Gamma Squeeze', feature it in Section 2.
       - SECONDARY (Strong Expansion Fallback): If zero pure Gamma Squeezes exist, DO NOT report the engine as flat. Inspect the phase breadth (e.g., Strong_Expansion pool) or the spread candidates. Select the top relative strength leaders and elevate them to the High-Conviction table with their key support/resistance levels.
       - TERTIARY (Tactical Options Spreads): You MUST always provide credit and debit spread plays under Section 3:
         * Bullish Bias: Bull Put Credit Spread (Support defense) or Bull Call Debit Spread.
         * Bearish Bias: Bear Call Credit Spread (Resistance ceiling) or Bear Put Debit Spread.
         * Neutral / Sideways: Iron Condor or Range-Bound Credit Spread.

    3. FORBIDDEN OUTPUTS:
       - NEVER write "The engine is flat", "zero actionable signals", or "No options structures qualified". The market always offers a directional spread or a premium-selling setup across the scanned universe.

    ---
    ### HTML DESIGN & STYLING:
    - Wrapper: Outer dark container (#121212) with 20px padding.
    - Card: Inner container (#F9F9F9), dark text (#111111), 8px border-radius, 24px padding, subtle drop shadow.
    - Tables: Full width <table>, dark slate header (#1E1E1E) with bold white text, alternating rows (#FFFFFF and #F0F0F0), cell padding 10px.
    - Highlights: Gold (#D4AF37) or deep green (#2E7D32) for strike levels and entry zones.

    ---
    ### REPORT STRUCTURE:

    1. 🎯 THE PRECISION TRADER: DAILY ACTION PLAN
       - H2 header, centered.
       - Executive summary breaking down active breadth (total scanned, count of Strong_Expansion vs Pullback, volatility alerts) and defining today's structural trading posture.

    2. 🔥 HIGH-CONVICTION SETUPS
       - H3 header.
       - HTML Table with columns:
         Ticker | Regime / Phase | Spot Price | Primary Structural Level | Conviction Rationale
       - Populated with Tier 1 squeezes, or Tier 2 expansion leaders if Tier 1 is absent.

    3. 🛠️ THE OPTIONS PLAYBOOK (ALWAYS POPULATED)
       - H3 header.
       - Create individual cards/blocks for at least 3 distinct spread candidates from the data:
         * Card 1: Bullish Spread (e.g., Bull Call Debit or Bull Put Credit)
         * Card 2: Bearish / Hedge Spread (e.g., Bear Call Credit or Bear Put Debit)
         * Card 3: Neutral / Volatility Harvest (Iron Condor or defined credit wing)
       - For each, provide: Spot Price, RSI/Momentum context, Long Strike, Short Strike, Expiry Horizon (1-4 DTE), and Net Target Credit/Debit.

    4. 🛡️ PRECISION RISK MANAGEMENT & EXECUTION RULES
       - H3 header.
       - Position Sizing (max 10%), Time Horizon (1-4 days), Profit Target (50%-70%), Stop Loss (15%-30%).

    5. 📉 CHARTING WATCHLIST
       - H3 header.
       - Bulleted list of active candidate tickers with their critical structural ceilings and floors.
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
