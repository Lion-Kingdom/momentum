import pandas as pd
import yfinance as yf
import google.generativeai as genai
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# --- 1. yfinance OHLCV Extraction ---
def append_ohlcv_data(master_csv_path="unified_gex_momentum_master_log.csv"):
    """Reads the master log, fetches latest OHLCV data for each ticker, and appends it."""
    print("Fetching OHLCV data...")
    df = pd.read_csv(master_csv_path)
    
    # Initialize new columns
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
            
    # Save the updated dataframe
    df.to_csv(master_csv_path, index=False)
    print("OHLCV data appended successfully.")
    return df

# --- 2. Gemini API Reporting ---
def generate_gemini_report(df):
    """Passes the Gamma data to Gemini to generate a Deep Dive Market Report."""
    print("Generating Gemini Deep Dive Report...")
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    
    # Filter for interesting regimes to feed the prompt
    negative_gamma = df[df['Market_Regime'] == 'NEGATIVE GAMMA'][['Ticker', 'Close', 'Call_Wall_Ceiling', 'Put_Wall_Floor', 'Confirmed_Strategy']]
    positive_gamma = df[df['Market_Regime'] == 'POSITIVE GAMMA'][['Ticker', 'Close', 'Call_Wall_Ceiling', 'Put_Wall_Floor', 'Confirmed_Strategy']]
    
    prompt = f"""
    You are an expert quantitative market analyst. Please review the following end-of-day Gamma data and write a 'Deep Dive Market Report'.
    Focus on the implications of the Negative and Positive Gamma regimes, potential volatility squeezes, and the dealer positioning (Call Walls/Put Walls).
    
    NEGATIVE GAMMA SETUPS (High Volatility Risk):
    {negative_gamma.to_string(index=False)}
    
    POSITIVE GAMMA SETUPS (Volatility Suppression / Pinning):
    {positive_gamma.to_string(index=False)}
    
    Format the report with clear headings, bullet points for actionable setups, and keep the tone professional and analytical.
    """
    
    # Using the Gemini 1.5 Flash model for fast, cost-effective text generation
    model = genai.GenerativeModel('gemini-1.5-flash')
    response = model.generate_content(prompt)
    
    return response.text

# --- 3. Email Delivery ---
def send_email_report(report_content):
    """Sends the generated report via email using smtplib."""
    print("Dispatching email report...")
    sender = os.getenv("EMAIL_USER")
    pwd = os.getenv("EMAIL_PASS")
    recipient = sender # Or add a list of recipients
    
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
    updated_df = append_ohlcv_data("unified_gex_momentum_master_log.csv")
    
    # 2. Generate the report with Gemini
    ai_report = generate_gemini_report(updated_df)
    print("\n--- GENERATED REPORT PREVIEW ---\n")
    print(ai_report)
    
    # 3. Send the email
    send_email_report(ai_report)
  
