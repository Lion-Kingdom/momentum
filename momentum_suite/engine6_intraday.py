import logging
import os
import re
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

import gspread
import pandas as pd
import requests
import urllib3
from google.oauth2.service_account import Credentials
from pyfinviz.screener import Screener

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

IBKR_BASE_URL: str = "https://localhost:5000/v1/api"
SPREADSHEET_ID = "19vJuI1ZE34h1weS8s3_RJEoWz6meVKMliFWvDjm5fc0"


# ==========================================
# 1. DISCOVERY MODULE
# ==========================================
def discover_engine6_targets() -> pd.DataFrame:
    """Scrapes Finviz via pyfinviz with Engine 6 small-cap filters."""
    logging.info("🔎 Scanning Finviz for Engine 6 momentum runners...")

    engine6_filters: List[Any] = [
        Screener.MarketCapOption.SMALL_UNDER_USD2BLN,
        Screener.FloatOption.UNDER_20M,
        Screener.PriceOption.UNDER_USD15,
        Screener.PriceOption.OVER_USD1,
        Screener.RelativeVolumeOption.OVER_3,
    ]

    try:
        screener = Screener(
            filter_options=engine6_filters,
            signal_option=Screener.SignalOption.TOP_GAINERS,
            view_option=Screener.ViewOption.OVERVIEW,
            pages=[1],
        )

        if not hasattr(screener, "data_frames") or 1 not in screener.data_frames:
            logging.warning("⚠️ pyfinviz returned no data.")
            return pd.DataFrame()

        dataframe: pd.DataFrame = screener.data_frames[1]

        if "Volume" in dataframe.columns:
            # --- FIX: Strip the duplicated first letter from Finviz ---
            dataframe["Ticker"] = dataframe["Ticker"].astype(str).str[1:]
            # ----------------------------------------------------------
            dataframe["Volume"] = pd.to_numeric(
                dataframe["Volume"].astype(str).str.replace(",", ""), errors="coerce"
            )
            dataframe["Price"] = pd.to_numeric(
                dataframe["Price"].astype(str).str.replace(r"[^0-9.]", "", regex=True),
                errors="coerce",
            )
            dataframe["Change_%"] = pd.to_numeric(
                dataframe["Change"].astype(str).str.replace("%", ""), errors="coerce"
            )

            # Apply Engine 6 Gap Rule (>= 4.0%)
            dataframe = dataframe[dataframe["Change_%"] >= 4.0]
            dataframe = dataframe.sort_values(by="Change_%", ascending=False).reset_index(drop=True)

        logging.info("✅ Discovery complete. Found %d runners.", len(dataframe))
        return dataframe

    except Exception as error:
        logging.error("❌ pyfinviz fetch failed: %s", error)
        return pd.DataFrame()


# ==========================================
# 2. IBKR & FALLBACK PRICING MODULE
# ==========================================
def get_contract_id(ticker_symbol: str) -> Optional[str]:
    """Resolves IBKR Contract ID if local gateway is active."""
    url = f"{IBKR_BASE_URL}/trsrv/stocks"
    try:
        res = requests.get(url, params={"symbols": ticker_symbol}, verify=False, timeout=2)
        if res.status_code == 200:
            data = res.json()
            if ticker_symbol in data and len(data[ticker_symbol]) > 0:
                contracts = data[ticker_symbol][0].get("contracts", [])
                if contracts:
                    return str(contracts[0].get("conid"))
    except Exception:
        pass
    return None


def get_market_snapshot(conid: str, ticker: str) -> Optional[Dict[str, Any]]:
    """Pings IBKR live market snapshot."""
    url = f"{IBKR_BASE_URL}/iserver/marketdata/snapshot"
    params = {"conids": conid, "fields": "31,84,86,87"}
    try:
        requests.get(url, params=params, verify=False, timeout=2)
        time.sleep(0.5)
        res = requests.get(url, params=params, verify=False, timeout=2)
        if res.status_code == 200 and res.json():
            snapshot = res.json()[0]
            raw_p = str(snapshot.get("31", "0.0"))
            clean_p = re.sub(r"[^0-9.]", "", raw_p)
            return {
                "Live_Price": round(float(clean_p), 2) if clean_p else 0.0,
                "Live_Bid": snapshot.get("84", "N/A"),
                "Live_Ask": snapshot.get("86", "N/A"),
                "Source": "IBKR Live"
            }
    except Exception:
        pass
    return None


def verify_and_enrich_targets(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Tries IBKR API first; falls back to Finviz/yfinance if offline."""
    enriched = []
    top_candidates = df.head(15)

    for _, row in top_candidates.iterrows():
        ticker = str(row["Ticker"])
        item = row.to_dict()
        conid = get_contract_id(ticker)

        snapshot = get_market_snapshot(conid, ticker) if conid else None

        if snapshot:
            item.update(snapshot)
        else:
            # Cloud Fallback Pricing
            item["Live_Price"] = round(float(row.get("Price", 0.0)), 2)
            item["Live_Bid"] = "N/A"
            item["Live_Ask"] = "N/A"
            item["Source"] = "Finviz/Cloud"

        enriched.append(item)

    return enriched


# ==========================================
# 3. EXPORT & REPORTING MODULES
# ==========================================
def export_to_sheets(targets: List[Dict[str, Any]]):
    """Logs snapshot results to Google Sheets in an Intraday tab."""
    key_json = os.getenv("GCP_SA_KEY")
    if not key_json:
        return

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(eval(key_json), scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID)

    tab_title = "Engine6_DayTrade_Snapshots"
    try:
        worksheet = sheet.worksheet(tab_title)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sheet.add_worksheet(title=tab_title, rows=1000, cols=10)
        worksheet.append_row(["Timestamp", "Ticker", "Price", "Change_%", "Volume", "Rel_Vol", "Source"])

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M EDT")
    rows_to_add = []
    for t in targets[:10]:
        rows_to_add.append([
            now_str,
            t.get("Ticker"),
            t.get("Live_Price"),
            t.get("Change_%"),
            t.get("Volume"),
            t.get("Rel Volume", "N/A"),
            t.get("Source")
        ])
    worksheet.append_rows(rows_to_add)
    logging.info("✅ Logged %d targets to Google Sheets.", len(rows_to_add))


def generate_email_report(targets: List[Dict[str, Any]]) -> str:
    """Builds the text snapshot email with TradingView export string."""
    top5 = targets[:5]
    tickers = [t["Ticker"] for t in top5]
    tv_string = ",".join(tickers)

    now_str = datetime.now().strftime("%b %d, %Y - %I:%M %p EDT")

    report = f"⚡ ENGINE 6 INTRADAY SNAPSHOT ({now_str})\n"
    report += f"{'='*50}\n\n"

    report += "🎯 TOP 5 DAY TRADE RUNNERS\n"
    report += f"{'='*50}\n"
    for i, t in enumerate(top5, 1):
        report += (f"{i}. {t['Ticker']:<5} | Price: ${t['Live_Price']:<6} | "
                   f"Change: +{t['Change_%']}% | Vol: {t.get('Volume', 'N/A')}\n")

    report += f"\n{'='*50}\n"
    report += "📋 TRADINGVIEW WATCHLIST EXPORT (Copy/Paste)\n"
    report += f"{'='*50}\n"
    report += f"{tv_string}\n\n"

    report += f"{'='*50}\n"
    report += f"🔗 Google Sheet Access Link:\n"
    report += f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit\n"

    return report


def send_email_snapshot(report_body: str):
    """Sends intraday snapshot to Gmail."""
    sender = os.getenv("EMAIL_USER")
    pwd = os.getenv("EMAIL_PASS")
    if not sender or not pwd:
        logging.warning("⚠️ Email secrets not configured. Skipping email dispatch.")
        return

    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = sender
    msg['Subject'] = f"⚡ Engine 6 Intraday Alert: Top 5 Runners ({datetime.now().strftime('%I:%M %p EDT')})"
    msg.attach(MIMEText(report_body, 'plain'))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender, pwd)
        server.send_message(msg)
        server.quit()
        logging.info("✅ Email snapshot successfully dispatched!")
    except Exception as e:
        logging.error("❌ Email failed: %s", e)


# ==========================================
# 4. MAIN ORCHESTRATION
# ==========================================
def main():
    logging.info("🚀 Running Engine 6 Intraday Pipeline...")
    raw_df = discover_engine6_targets()

    if raw_df.empty:
        logging.info("🛑 Scanner complete. No active Engine 6 runners found.")
        return

    targets = verify_and_enrich_targets(raw_df)

    # Export to Google Sheets & Email
    export_to_sheets(targets)
    report = generate_email_report(targets)
    print("\n" + report)
    send_email_snapshot(report)


if __name__ == "__main__":
    main()
