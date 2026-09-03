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
    """ Passes the filtered Gamma data to Gemini to generate a Deep Dive Market Report."""
    print("Generating Gemini Deep Dive Report...")

    # 1. FILTER FIRST: Only keep rows that are actual high-conviction squeezes or negative gamma setups
    # (Adjust these column names to match whatever exact flags your engine writes to the CSV)
    # Search for the target phrases inside both the Momentum_Signal and Market_Regime columns
    filtered_df = df[
        df['Momentum_Signal'].str.contains('Gamma Squeeze|Breakout', case=False, na=False) |
        df['Market_Regime'].str.contains('Negative Gamma', case=False, na=False)
        ]
    
    # Fallback: if the filter is too tight and returns empty, pass the head so it doesn't crash
    if filtered_df.empty:
        filtered_df = df.head(15)

    # 2. Convert ONLY the filtered rows to CSV
    report_data = filtered_df.to_csv(index=False)
    
    prompt = f"""
    You are the lead quantitative analyst for 'The Precision Trader'. Analyze the provided options CSV data.
    
    CRITICAL SCREENING RULES:
    You MUST prioritize and extract any ticker, especially ETFs and Indexes (like SPX, XSP, NDX, RUT), that meet the following criteria:
    1. The Gamma Squeeze: If the row explicitly states 'High-Conviction Gamma Squeeze' or 'Negative Gamma', it MUST be featured in the 'High-Conviction Setups' section. Do not output a flat day if this condition exists.
    2. The 'Before It Happens' Setup: Analyze the OHLCV data against the Call Wall. If a ticker is operating in 'Negative Gamma' and the 'Close' price is within 1.5% of the 'Call_Wall_Ceiling', flag it as 'Approaching Squeeze Trigger'.

    Here is the daily filtered data:
    {report_data}

    Format the final output STRICTLY as raw HTML. DO NOT wrap the output in markdown code blocks (e.g., no ```html). Just output the raw HTML tags. 
    
    Design Requirements (Inline CSS):
    - Background: Wrap the entire email in a container with a dark gray background (#121212) and padding.
    - Card: Create a main inner container (the "Trade Card") with a white or off-white background (#F9F9F9), dark text (#111111), rounded corners (8px), and a subtle box shadow.
    - Typography: Use a modern sans-serif font family (Arial, Helvetica, sans-serif).
    - Tables: When displaying data (like the High-Conviction Setups), use an HTML <table> with a dark header row, bold text, and alternating light gray rows.
    - Highlights: Use bold text and gold (#D4AF37) or green (#2E7D32) font colors to highlight key strike prices and target zones.

    Follow this exact content structure inside the HTML:

    1. 🎯 THE PRECISION TRADER: DAILY ACTION PLAN (Use an H2 or H1 tag, centered, maybe with a dark background banner)
    Write a 2-3 sentence punchy, high-energy market overview based on the data.

    2. 🔥 HIGH-CONVICTION SETUPS (H3 tag)
    (If there are no actionable setups today, state: "The engine is flat today...")
    (If there ARE setups, output a clean HTML table with these columns: Ticker, Current Price, Structural Support, Upside Target, Momentum Profile).

    3. 🛠️ THE OPTIONS PLAYBOOK (H3 tag)
    (For each ticker, create a clean visual block with 3 actionable options strategies: Conservative, Aggressive, Ultra Aggressive. Use bullet points or styled div boxes).

    4. 🛡️ PRECISION RISK MANAGEMENT & EXECUTION RULES (H3 tag)
    (Include our strict rules: Position Sizing (max 10%), Time Horizon (1-4 days), Take Profit (50-70%), Stop Loss (15-30%)).

    5. 📉 CHARTING WATCHLIST (H3 tag)
    (Comma-separated list of active tickers).
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
