"""
Moomoo API Options Gamma Screener and Momentum Pipeline.
Calculates options GEX (Gamma Exposure) to identify market regimes.
"""

import json
import os
import random
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import gspread
import numpy as np
import pandas as pd
import requests
from oauth2client.service_account import ServiceAccountCredentials


SPREADSHEET_ID = "19vJuI1ZE34h1weS8s3_RJEoWz6meVKMliFWvDjm5fc0"
MOOMOO_API_URL = "https://webapi.moomoo.com/api/v1.0"


def get_moomoo_headers():
    """Builds the authorization headers using the OAuth token."""
    token = os.getenv("MOOMOO_API_TOKEN", "")
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }


def refresh_moomoo_token():
    """Refreshes the Moomoo API access token using the refresh token."""
    print("🔄 Attempting to refresh Moomoo API token...")

    client_id = os.getenv("MOOMOO_CLIENT_ID")
    refresh_token = os.getenv("MOOMOO_REFRESH_TOKEN")

    if not client_id or not refresh_token:
        print("❌ Cannot refresh token: Missing Credentials in GitHub Secrets.")  # noqa: E501
        return False

    url = "https://webapi.moomoo.com/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }

    try:
        response = requests.post(url, headers=headers, data=payload, timeout=10)
        data = response.json()

        if "access_token" in data:
            os.environ["MOOMOO_API_TOKEN"] = data["access_token"]
            print("✅ Successfully refreshed Moomoo API access token!")
            return True
        print(f"❌ Failed to refresh token. Server returned: {data}")
        return False
    except requests.RequestException as e:
        print(f"❌ Network error during token refresh: {e}")
        return False


def verify_moomoo_authentication():
    """Pings the API to verify the token is valid, refreshing if needed."""
    print("🔐 Verifying Moomoo API Authentication...")
    url = f"{MOOMOO_API_URL}/quote/stock-basicinfo"
    payload = {"code_list": ["US.AAPL"]}

    try:
        response = requests.post(
            url, headers=get_moomoo_headers(), json=payload, timeout=10
        )

        if response.status_code in [401, 403]:
            print(f"⚠️ Auth Failed (HTTP {response.status_code}). Refreshing...")
            if refresh_moomoo_token():
                response = requests.post(
                    url, headers=get_moomoo_headers(), json=payload, timeout=10
                )
            else:
                return False

        data = response.json()
        if data.get("ret_code") == 0:
            print("✅ Moomoo API Authentication Successful!\n" + "=" * 60)
            return True
        print(f"⚠️ Auth check returned an API error: {data}")
        return False

    except requests.RequestException as e:
        print(f"❌ Failed to reach Moomoo API during auth check: {e}")
        return False


def map_tickers_to_stock_ids(tickers):
    """Fetches internal Moomoo stock_ids for a list of standard tickers."""
    url = f"{MOOMOO_API_URL}/quote/stock-basicinfo"
    moomoo_codes = [f"US.{ticker}" for ticker in tickers]
    mapping = {}

    chunk_size = 350
    for i in range(0, len(moomoo_codes), chunk_size):
        payload = {"code_list": moomoo_codes[i:i + chunk_size]}

        try:
            response = requests.post(
                url, headers=get_moomoo_headers(), json=payload, timeout=15
            )
            data = response.json()

            if data.get("ret_code") == 0:
                securities = data.get("data", {}).get("basic_list", [])
                for sec in securities:
                    clean_ticker = sec.get("code", "").replace("US.", "")
                    stock_id = sec.get("stock_id")
                    if clean_ticker and stock_id:
                        mapping[clean_ticker] = stock_id
            else:
                print(f"🛑 HTTP Status Code: {response.status_code}")
                print(f"🛑 Raw Server Response: {response.text}")
                print(f"⚠️ Moomoo mapping error: {data.get('ret_msg', data)}")
        except requests.RequestException as e:
            print(f"❌ Failed to map chunk: {e}")

    return mapping


def get_moomoo_options_data(stock_id, ticker=""):
    """Pulls option chains with a dynamic limit based on asset tier."""
    url = f"{MOOMOO_API_URL}/quote/option-screen"

    heavy_tickers = ["SPX", "NDX", "RUT", "AAPL", "TSLA", "NVDA"]
    fetch_limit = 300 if ticker in heavy_tickers else 100

    payload = {
        "strategy": {
            "market_category_list": [0],
            "filter_group_list": [
                {
                    "underlying_list": [
                        {
                            "indicator_type": 101,
                            "indicator_value": {"value_list": [stock_id]},
                        }
                    ]
                }
                # 🟢 THE FIX: Completely removed the "Option Type" filter block.
                # By not filtering the type, Moomoo natively returns BOTH Calls and Puts!
            ],
        },
        "field_filter": {
            "hp_strike_price": 1,
            "option_type": 1,
            "price": 1,
            "volume": 1,
            "open_interest": 1,
            "implied_volatility": 1,
            "gamma": 1,
            "delta": 1,
            "option_name": "x",
            "underlying_info": {"price": 1},
        },
        "sort_obj": {"sort_field": {"volume": 1}},
        "limit": fetch_limit,
    }

    max_retries = 3

    for attempt in range(max_retries):
        try:
            time.sleep(0.6 + random.uniform(0.1, 0.3))
            response = requests.post(
                url, headers=get_moomoo_headers(), json=payload, timeout=15
            )
            data = response.json()

            if data.get("ret_code") == 0:
                return data.get("data", {}).get("option_list", [])

            err_msg = data.get("ret_msg", "")

            if "rate limit" in err_msg.lower():
                sleep_time = (2 ** attempt) + random.uniform(0.5, 1.0)
                print(f"⚠️ Rate limit hit for {ticker}. Backing off for {sleep_time:.1f}s...")  # noqa
                time.sleep(sleep_time)
                continue

            print(f"⚠️ Moomoo option-screen warning for {ticker}: {err_msg}")
            return []

        except requests.RequestException as e:
            print(f"❌ Connection error for {ticker}: {e}")
            time.sleep(1)

    print(f"❌ Max retries exceeded for {ticker}.")
    return []


def export_gex_to_sheets(gex_dataframe):
    """Pushes the final GEX dataframe directly to a Google Sheet."""
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
        sheet.update(
            [gex_dataframe.columns.values.tolist()] + gex_dataframe.values.tolist()
        )
        print("✅ GEX Report successfully pushed to Google Sheets!")
    except Exception as e:  # pylint: disable=broad-except
        print(f"❌ Failed to push to Google Sheets: {e}")


def send_email_gex_report(gex_dataframe):
    """Emails the GEX targets to the subscriber group."""
    sender = os.getenv("EMAIL_USER")
    pwd = os.getenv("EMAIL_PASS")

    if not sender or not pwd:
        print("⚠️ Email secrets not configured. Skipping email dispatch.")
        return

    mailing_list = [sender, "new_being@hotmail.com"]
    now_str = datetime.now().strftime("%b %d, %Y - %I:%M %p EDT")

    msg = MIMEMultipart()
    msg["From"] = f'"Leon EL Cee" <{sender}>'
    msg["To"] = sender
    msg["Subject"] = f"🎯 Institutional GEX Setup Report ({now_str})"

    body = f"⚡ MOOMOO API GEX PIPELINE SNAPSHOT ({now_str})\n{'=' * 50}\n\n"

    if not gex_dataframe.empty:
        for _, row in gex_dataframe.iterrows():
            body += f"🎯 {row['Ticker']} | {row['Timeframe']} | Signal: {row['Momentum_Signal']}\n"  # noqa: E501
            body += f"   Strategy: {row['Confirmed_Strategy']}\n"
            body += f"   Targets: {row['Target_Strikes']}\n"
            body += f"   Regime: {row['Market_Regime']}\n\n"
    else:
        body += "No active GEX setups found for this session.\n\n"

    body += f"{'=' * 50}\n🔗 Google Sheet Access Link: \nhttps://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit\n"  # noqa

    msg.attach(MIMEText(body, "plain"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender, pwd)
        server.sendmail(sender, mailing_list, msg.as_string())
        server.quit()
        print(f"✅ GEX snapshot successfully sent to {len(mailing_list)} subscribers!")
    except smtplib.SMTPException as e:
        print(f"❌ Email failed: {e}")


def process_pipeline_batch(momentum_csv_path="momentum_signals.csv"):
    """Reads momentum spreadsheet and calculates GEX walls using Moomoo."""
    if not verify_moomoo_authentication():
        print("🛑 Pipeline halted: Moomoo authentication failure.")
        return

    if not os.path.exists(momentum_csv_path):
        print(f"⚠️ Momentum spreadsheet '{momentum_csv_path}' not found.")
        return

    print(f"📂 Reading filtered momentum targets from: {momentum_csv_path}...")
    df_momentum = pd.read_csv(momentum_csv_path)

    active_targets = df_momentum[df_momentum["Momentum_Signal"].notna()]
    if active_targets.empty:
        print("⚠️ No active momentum signals found.")
        return

    raw_tickers = (
        active_targets["Ticker"].str.replace("^", "", regex=False).unique().tolist()
    )
    print(f"🔗 Mapping {len(raw_tickers)} tickers to Moomoo Stock IDs...")
    ticker_to_id = map_tickers_to_stock_ids(raw_tickers)

    main_results = []
    today = pd.Timestamp.today().normalize()

    for _, row in active_targets.iterrows():
        raw_ticker = str(row["Ticker"]).strip().upper()
        clean_ticker = raw_ticker.replace("^", "")
        mom_signal = row["Momentum_Signal"]
        is_index = raw_ticker.startswith("^")

        print(f"\n{'=' * 60}\n🔄 Processing: {clean_ticker} | Signal: {mom_signal}\n{'=' * 60}")  # noqa: E501

        stock_id = ticker_to_id.get(clean_ticker)
        if not stock_id:
            print(f"⚠️ {clean_ticker}: Could not resolve Moomoo Stock ID. Skipping.")
            continue

        options_data = get_moomoo_options_data(stock_id, clean_ticker)
        if not options_data:
            print(f"⚠️ {clean_ticker}: No options data returned. Skipping.")
            continue

        spot_price = options_data[0].get("underlying", {}).get("price", 0.0)
        if spot_price == 0.0:
            print(f"⚠️ {clean_ticker}: Missing underlying spot price.")
            continue

        parsed_options = []
        for opt in options_data:
            opt_name = opt.get("option_name", "")

            raw_type = str(opt.get("option_type", "")).upper()
            if raw_type in ["1", "3", "CALL", "C"]:
                opt_type = "CALL"
            elif raw_type in ["2", "4", "PUT", "P"]:
                opt_type = "PUT"
            else:
                opt_type = raw_type

            try:
                date_str = opt_name.split(" ")[1]
                exp_date = pd.to_datetime(date_str, format="%y%m%d")
                dte = (exp_date - today).days

                parsed_options.append(
                    {
                        "strike": float(opt.get("strike_price", 0.0)),
                        "type": opt_type,
                        "gamma": float(opt.get("gamma", 0.0)),
                        "open_interest": float(opt.get("open_interest", 0.0) or 0.0),
                        "dte": dte,
                        "exp_date": exp_date.strftime("%Y-%m-%d"),
                    }
                )
            except (IndexError, ValueError):
                continue

        df_opts = pd.DataFrame(parsed_options)
        if df_opts.empty:
            continue

        target_buckets = [0, 7, 28] if is_index else [7, 28]

        for bucket in target_buckets:
            closest_idx = (df_opts["dte"] - bucket).abs().argsort()[:1]
            closest_dte = df_opts.iloc[closest_idx]["dte"].values[0]
            if closest_dte < 0:
                continue

            bucket_df = df_opts[df_opts["dte"] == closest_dte]
            target_expiry = bucket_df["exp_date"].iloc[0]
            bucket_label = f"~{bucket} DTE"

            print(f"   ⏱️ Evaluating {bucket_label} -> Expiry: {target_expiry} ({closest_dte} DTE)")  # noqa: E501

            calls = bucket_df[bucket_df["type"] == "CALL"].copy()
            puts = bucket_df[bucket_df["type"] == "PUT"].copy()

            if calls.empty or puts.empty:
                print(f"      ⚠️ Missing options in chain (Calls: {len(calls)}, Puts: {len(puts)}).")  # noqa: E501
                continue

            calls["Call_GEX"] = (calls["gamma"] * calls["open_interest"] * 100 * spot_price) / 1_000_000  # noqa: E501
            puts["Put_GEX"] = (-puts["gamma"] * puts["open_interest"] * 100 * spot_price) / 1_000_000  # noqa: E501

            df_calls = calls[["strike", "Call_GEX", "open_interest"]].rename(
                columns={"open_interest": "Call_OI"}
            )
            df_puts = puts[["strike", "Put_GEX", "open_interest"]].rename(
                columns={"open_interest": "Put_OI"}
            )

            combined = pd.merge(df_calls, df_puts, on="strike", how="outer").fillna(0)
            combined["Net_GEX_Millions"] = combined["Call_GEX"] + combined["Put_GEX"]

            call_oi_sum = combined["Call_OI"].sum()
            put_oi_sum = combined["Put_OI"].sum()

            if call_oi_sum == 0 or put_oi_sum == 0:
                print(f"      ⚠️ Zero OI found (Call OI: {call_oi_sum}, Put OI: {put_oi_sum}).")  # noqa: E501
                continue

            call_wall_strike = combined.loc[combined["Call_OI"].idxmax(), "strike"]
            put_wall_strike = combined.loc[combined["Put_OI"].idxmax(), "strike"]

            combined["dist_to_spot"] = abs(combined["strike"] - spot_price)
            local_zone = combined[combined["dist_to_spot"] < (spot_price * 0.06)].sort_values("strike")  # noqa: E501

            flip_strike = spot_price
            for i in range(len(local_zone) - 1):
                gex_1 = local_zone["Net_GEX_Millions"].iloc[i]
                gex_2 = local_zone["Net_GEX_Millions"].iloc[i + 1]
                if np.sign(gex_1) != np.sign(gex_2):
                    flip_strike = local_zone["strike"].iloc[i]
                    break

            regime = "POSITIVE GAMMA" if spot_price > flip_strike else "NEGATIVE GAMMA"
            strategy = "Stand Aside (Conflicting Signals)"
            targets = "N/A"

            if mom_signal == "Bullish" and spot_price > put_wall_strike:
                valid_puts = combined[combined["strike"] <= put_wall_strike].sort_values("strike", ascending=False)  # noqa
                if not valid_puts.empty:
                    strategy = "Bull Put Credit Spread"
                    targets = f"Short Put: ${valid_puts.iloc[0]['strike']:,.2f}" # noqa
            elif mom_signal == "Bearish" and spot_price < call_wall_strike:
                valid_calls = combined[combined["strike"] >= call_wall_strike].sort_values("strike")  # noqa
                if not valid_calls.empty:
                    strategy = "Bear Call Credit Spread"
                    targets = f"Short Call: ${valid_calls.iloc[0]['strike']:,.2f}"  # noqa

            conviction = "High" if mom_signal == "Breakout" else "Standard"

            print(f"      📊 WALLS & REGIME -> Call Wall: ${call_wall_strike:,.2f} | Put Wall: ${put_wall_strike:,.2f} | Flip: ${flip_strike:,.2f}")  # noqa
            print(f"      📈 MARKET REGIME -> {regime} (Spot: ${spot_price:,.2f})") # noqa
            print(f"      ✅ Verified -> {strategy} | Target: {targets}")

            main_results.append(
                {
                    "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "Ticker": raw_ticker,
                    "Sector": "Moomoo Default",
                    "Conviction": conviction,
                    "Timeframe": bucket_label,
                    "Momentum_Signal": mom_signal,
                    "Market_Regime": regime,
                    "Spot_Price": spot_price,
                    "Target_Expiry": target_expiry,
                    "Actual_DTE": closest_dte,
                    "Call_Wall_Ceiling": call_wall_strike,
                    "Put_Wall_Floor": put_wall_strike,
                    "Gamma_Flip": flip_strike,
                    "Confirmed_Strategy": strategy,
                    "Target_Strikes": targets,
                }
            )

    if main_results:
        main_df = pd.DataFrame(main_results)
        main_df = main_df.sort_values(by=["Ticker", "Actual_DTE"])
        main_filename = "unified_gex_momentum_main_log.csv"
        main_df.to_csv(main_filename, index=False)
        print(f"\n{'=' * 60}\n💾 Main Pipeline Log Saved: {main_filename}\n{'=' * 60}")
        # Uncomment below when ready to push live
        # export_gex_to_sheets(main_df)
        # send_email_gex_report(main_df)
    else:
        print(f"\n{'=' * 60}\n⚠️ Pipeline finished but NO active setups to save.\n{'=' * 60}")  # noqa


if __name__ == "__main__":
    process_pipeline_batch("momentum_suite/momentum_signals.csv")
