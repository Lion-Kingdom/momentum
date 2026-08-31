import pandas as pd
import yfinance as yf
from google import genai
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime


# --- 1. yfinance OHLCV Extraction & Data Pruning ---
def append_ohlcv_data(master_csv_path="unified_gex_momentum_master_log.csv"):
    """Reads the master log, fetches latest OHLCV data, and enforces a 5-day retention policy."""
    print("Fetching OHLCV data...")
    df = pd.read_csv(master_csv_path)
    
    # Initialize new columns
    if 'Close' not in df.columns:
        df['Close'] = 0.0
        df['Volume'] = 0
    
    for index, row in df.iterrows():
        ticker = row['Ticker']
        # Handle index tickers for yfinance
        yf_ticker = f"^{ticker}" if ticker in ["SPX", "NDX", "RUT", "VIX"] and not ticker.startswith("^") else ticker
        
        try:
            stock = yf.Ticker(yf_ticker)
            hist = stock.history(period="1d")
            if not hist.empty:
                df.at[index, 'Close'] = hist['Close'].iloc[-1]
                df.at[index, 'Volume'] = hist['Volume'].iloc[-1]
        except Exception as e:
            print(f"Error fetching OHLCV for {ticker}: {e}")
            
    # --- 5-DAY ROLLING RETENTION LOGIC ---
    print("Applying 5-day rolling data retention...")
    df['Timestamp'] = pd.to_datetime(df['Timestamp'])
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=5)
    df = df[df['Timestamp'] >= cutoff]
    # ---------------------------------------
            
    # Save the updated and pruned dataframe
    df.to_csv(master_csv_path, index=False)
    print("OHLCV data appended and old records pruned successfully.")
    return df


# --- 2. Gemini API Reporting ---
def generate_gemini_report(df):
    """Passes the filtered Gamma data to Gemini to generate a Deep Dive Market Report."""
    print("Generating Gemini Deep Dive Report...")
    # --- NEW GENAI SDK SYNTAX ---
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    # 1. Filter out "Stand Aside" setups so we only pass actionable trades
    active_df = df[~df['Confirmed_Strategy'].str.contains("Stand Aside", na=False)]
    stand_aside_df = df[df['Confirmed_Strategy'].str.contains("Stand Aside", na=False)]
    
    # 2. Split active trades into regimes
    negative_gamma = active_df[active_df['Market_Regime'] == 'NEGATIVE GAMMA'][['Ticker', 'Close', 'Call_Wall_Ceiling', 'Put_Wall_Floor', 'Confirmed_Strategy']]
    positive_gamma = active_df[active_df['Market_Regime'] == 'POSITIVE GAMMA'][['Ticker', 'Close', 'Call_Wall_Ceiling', 'Put_Wall_Floor', 'Confirmed_Strategy']]
    
    # 3. Get a quick summary of the sidelines tickers
    stand_aside_tickers = stand_aside_df['Ticker'].unique().tolist()
    stand_aside_sample = ", ".join(stand_aside_tickers[:10]) + ("..." if len(stand_aside_tickers) > 10 else "")
    
    prompt = f"""
    You are an expert quantitative market analyst. Please review the following Gamma data and write our standard 'Deep Dive Market Report'.
    Focus on the implications of the Negative and Positive Gamma regimes, potential volatility squeezes, and the dealer positioning (Call Walls/Put Walls).
    
    For the ACTIVE SETUPS provided below, you MUST explicitly output actionable trading intelligence. Do not provide vague summaries. You must include:
    1. The exact Options Strategy structure recommended (e.g., Long Call, Bull Put Credit Spread, Bear Call Credit Spread).
    2. The specific Strike Price placement relative to the Call Wall and Put Wall data provided.
    3. The precise Delta parameters for the structure (e.g., Buy 0.30 Delta, Sell 0.15 Delta).
    
    ACTIVE NEGATIVE GAMMA SETUPS (High Volatility Risk):
    {negative_gamma.to_string(index=False) if not negative_gamma.empty else "No actionable negative gamma setups today."}
    
    ACTIVE POSITIVE GAMMA SETUPS (Volatility Suppression / Pinning):
    {positive_gamma.to_string(index=False) if not positive_gamma.empty else "No actionable positive gamma setups today."}
    
    STAND ASIDE SUMMARY:
    There are {len(stand_aside_tickers)} tickers exhibiting conflicting signals today (including: {stand_aside_sample}). 
    Provide a single brief sentence acknowledging these are being avoided due to a lack of statistical edge. Do not analyze them individually.
    
    IMPORTANT - WATCHLIST EXPORT:
    At the very bottom of the report, you MUST include a "Watchlist Export" section. This must be a clean, comma-separated list of ONLY the tickers from the ACTIVE SETUPS recommended above, so they can be copy-pasted into charting software.
    """

    response = client.models.generate_content(
        model='gemini-1.5-flash',
        contents=prompt
    )
    
    return response.text


# --- 3. Email Delivery ---
def send_email_report(report_content):
    """Sends the generated report via email using smtplib."""
    print("Dispatching email report...")
    sender = os.getenv("EMAIL_USER")
    pwd = os.getenv("EMAIL_PASS")
    recipient = sender  # Or add a list of recipients
    
    if not sender or not pwd:
        print("⚠️ Email secrets not configured. Skipping email dispatch.")
        return

    now_str = datetime.now().strftime("%b %d, %Y")
    
    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = recipient
    msg['Subject'] = f"🧠 AI Deep Dive Market Report: Gamma Regimes ({now_str})"
    
    msg.attach(MIMEText(report_content, 'plain'))
    
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender, pwd)
        server.sendmail(sender, recipient, msg.as_string())
        server.quit()
        print("✅ AI Report successfully delivered to inbox!")
    except Exception as e:
        print(f"❌ Email failed: {e}")


if __name__ == "__main__":
    # 1. Update the CSV with yfinance data (ADDED THE FOLDER PATH HERE)
    updated_df = append_ohlcv_data("momentum_suite/unified_gex_momentum_master_log.csv")
    
    # 2. Generate the report with Gemini
    ai_report = generate_gemini_report(updated_df)
    
    # 3. Send the email
    send_email_report(ai_report)
  
