import pandas as pd
import yfinance as yf
from google import genai
import smtplib
import os
import time  # <--- ADD THIS
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
        # Handle index tickers for yfinance
        yf_ticker = f"^{ticker}" if ticker in ["SPX", "XSP", "NDX", "RUT", "VIX"] and not ticker.startswith("^") else ticker
        
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
    # ---------------------------------------
            
    # Save the updated and pruned dataframe
    df.to_csv(master_csv_path, index=False)
    print("OHLCV data appended and old records pruned successfully.")
    return df


# --- 2. Gemini API Reporting ---
def generate_gemini_report(df):
    """Passes the filtered Gamma data to Gemini to generate a Deep Dive Market Report."""
    print("Generating Gemini Deep Dive Report...")
    
    # --- ADD THIS LINE RIGHT HERE ---
    report_data = df.to_markdown(index=False)
    
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
    You are the elite quantitative AI engine for 'The Precision Trader' platform. 
    Your audience consists of retail options traders who want highly visual, easy-to-read, and actionable trade setups. 
    Do NOT use overly complex institutional jargon (e.g., GEX, dealer delta-hedging, gamma regimes, pinning). 
    Instead, translate the quantitative data into aggressive, high-energy, momentum-based terminology (e.g., "High-Conviction Breakouts", "Support Zones", "Momentum Squeezes").

    Here is the daily filtered data:
    {report_data}

    Format the email EXACTLY like this using Markdown:

    # 🎯 The Precision Trader: Daily Action Plan
    Write a 2-3 sentence punchy, high-energy market overview based on the data. Mention that all tickers below have already passed our strict technical gauntlet (trading above key moving averages with strong RSI momentum) before even reaching this list.

    ---

    ## 🔥 High-Conviction Setups
    If there are no actionable setups today, state: "The engine is flat today. No tickers met our strict criteria. We protect capital and wait for the perfect pitch."
    
    If there ARE setups, create a clean Markdown table with these columns:
    | Ticker | Current Price | Structural Support | Upside Target | Momentum Profile |
    (Fill in the table using the data provided. Use plain English for the momentum profile, e.g., "Bullish Breakout").

    ---

    ## 🛠️ The Options Playbook
    For each ticker identified above, provide 3 actionable options strategies based on the current price and support/resistance levels. Format it beautifully:

    ### [TICKER SYMBOL] - Options Strategies
    *Why we like it:* (1 sentence explaining the technical strength and breakout potential).
    *   **Conservative Play (Income Generation):** Suggest a credit spread (e.g., Bull Put Spread). Name the strikes based on the structural support level.
    *   **Aggressive Play (Directional Spread):** Suggest a debit spread (e.g., Call Debit Spread). Name the strikes targeting the upside target.
    *   **Ultra Aggressive (Swing Trade):** Suggest a direct Long Call or Long Put for maximum leverage and momentum capture. Name the specific strike and logic.

    ---

    ## 🛡️ Precision Risk Management & Execution Rules
    (Include exactly these rules word-for-word to guide our subscribers):
    *   **Position Sizing:** Never risk more than **10%** of your dedicated options account on a single play (e.g., max $500 risk on a $5k account). When trading naked Long Calls/Puts, target premiums under **$5.00** (absolute max of $8.00).
    *   **Time Horizon:** We are hunting for momentum breakouts. If the breakout does not trigger within **1 to 4 days max**, cut the trade. Time decay is the enemy.
    *   **Take Profit:** Secure gains at **50% to 70%** on all Credit and Debit Spreads.
    *   **Stop Loss (Strict!):** Set automatic hard stops at **15% to 30%**. If you take a heavier Long Call/Put closer to the $8.00 max, tighten that stop loss strictly to **20%**. 

    ---

    ## 📉 Charting Watchlist (Export)
    Provide ONLY a comma-separated list of the active tickers (e.g., AAPL, TSLA, OKE) so subscribers can easily copy and paste them into ThinkOrSwim or TradingView. If none, write "NONE".
    """
    # --- NEW GENAI SDK SYNTAX ---
    # --- BULLETPROOF API CALL WITH AUTO-RETRY ---
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model='gemini-3.6-flash',
                contents=prompt
            )
            return response.text
            
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
                # If it's a different kind of error, crash and report it immediately
                raise e
    

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
    # 1. Update the CSV with yfinance data
    updated_df = append_ohlcv_data("momentum_suite/unified_gex_momentum_master_log.csv")
    
    # 2. Generate the report with Gemini
    ai_report = generate_gemini_report(updated_df)
    
    # 3. Read the FULL Momentum Stats Report generated by the first script
    try:
        with open("momentum_suite/momentum_summary.txt", "r", encoding="utf-8") as f:
            momentum_stats = f.read()
    except FileNotFoundError:
        momentum_stats = "(Momentum detailed stats unavailable for this run)"
        
    # 4. Merge them: AI Intelligence Playbook AT THE TOP, Full Stats/Spreads AT THE BOTTOM
    final_master_report = f"{ai_report}\n\n{'-'*60}\n\n{momentum_stats}"
    
    # 5. Send the ONE final combined email
    send_email_report(final_master_report)
  
