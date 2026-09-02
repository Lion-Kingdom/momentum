from datetime import datetime
import numpy as np
import pandas as pd
import scipy.stats as si
import yfinance as yf
import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

spreadsheet_id = "19vJuI1ZE34h1weS8s3_RJEoWz6meVKMliFWvDjm5fc0"


# --- BLACK-SCHOLES MATHEMATICAL ENGINE ---
def bs_gamma(s, k, t, r, sigma, q=0.015):
    """Calculates exact analytical Black-Scholes-Merton Gamma accounting for dividend yield."""
    if t <= 0 or sigma <= 0 or s <= 0 or k <= 0:
        return 0
    # Subtract q from the drift term in d1
    d1 = (np.log(s / k) + (r - q + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
    # Multiply the numerator by the continuous dividend discount factor
    gamma = (np.exp(-q * t) * si.norm.pdf(d1)) / (s * sigma * np.sqrt(t))
    return gamma


def export_gex_to_sheets(gex_dataframe):
    """Pushes the final GEX dataframe directly to a Google Sheet."""
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = json.loads(os.environ["GCP_SA_KEY"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)

        # Ensure you have a tab named "GEX_Report" created in your sheet
        sheet = client.open_by_key(spreadsheet_id).worksheet("GEX_Report")

        sheet.clear()
        sheet.update([gex_dataframe.columns.values.tolist()] + gex_dataframe.values.tolist())
        print("✅ GEX Report successfully pushed to Google Sheets!")
    except Exception as e:
        print(f"❌ Failed to push to Google Sheets: {e}")


def send_email_gex_report(gex_dataframe):
    """Emails the GEX targets to the subscriber group."""
    sender = os.getenv("EMAIL_USER")
    pwd = os.getenv("EMAIL_PASS")

    if not sender or not pwd:
        print("⚠️ Email secrets not configured. Skipping email dispatch.")
        return

    # Update your list with any new subscribers here
    mailing_list = [
        sender,
        "new_being@hotmail.com"
    ]

    now_str = datetime.now().strftime("%b %d, %Y - %I:%M %p EDT")

    msg = MIMEMultipart()
    msg['From'] = f'"Leon EL Cee" <{sender}>'
    msg['To'] = sender  # BCC routing
    msg['Subject'] = f"🎯 Options floor-ceiling Gamma(GEX) Setup Report ({now_str})"

    # Simple formatted email body
    body = f"⚡ GEX PIPELINE SNAPSHOT ({now_str})\n"
    body += f"{'='*50}\n\n"

    # Create a clean string representation of the key columns
    if not gex_dataframe.empty:
        for _, row in gex_dataframe.iterrows():
            body += f"🎯 {row['Ticker']} | {row['Timeframe']} | Signal: {row['Momentum_Signal']}\n"
            body += f"   Strategy: {row['Confirmed_Strategy']}\n"
            body += f"   Targets: {row['Target_Strikes']}\n"
            body += f"   Regime: {row['Market_Regime']}\n\n"
    else:
        body += "No active GEX setups found for this session.\n\n"

    body += f"{'='*50}\n"
    body += f"🔗 Google Sheet Access Link: \n"
    # REPLACE WITH YOUR SPREADSHEET ID BELOW
    body += f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit\n" # noqa

    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender, pwd)
        server.sendmail(sender, mailing_list, msg.as_string())
        server.quit()
        print(f"✅ GEX snapshot successfully sent to {len(mailing_list)} subscribers!")
    except Exception as e:
        print(f"❌ Email failed: {e}")


def process_pipeline_batch(momentum_csv_path="momentum_signals.csv"):
    """Reads momentum spreadsheet, calculates multi-timeframe GEX walls (0, 7, 28+ DTE),
    and logs precise trade support/resistance levels to a master CSV.
    """
    if not os.path.exists(momentum_csv_path):
        print(f"⚠️ Momentum spreadsheet '{momentum_csv_path}' not found.")
        return

    print(f"📂 Reading filtered momentum targets from: {momentum_csv_path}...")
    df_momentum = pd.read_csv(momentum_csv_path)

    if "Ticker" not in df_momentum.columns or "Momentum_Signal" not in df_momentum.columns:
        print("❌ Error: Momentum CSV must contain 'Ticker' and 'Momentum_Signal' columns.")
        return

    active_targets = df_momentum[df_momentum["Momentum_Signal"].isin(["Bullish", "Bearish", "Breakout"])]

    if active_targets.empty:
        print("⚠️ No active momentum signals found to process in GEX engine.")
        return

    print(f"🎯 Found {len(active_targets)} active momentum ticker(s) to evaluate against GEX walls.")
    master_results = []

    for _, row in active_targets.iterrows():
        raw_ticker = str(row["Ticker"]).strip().upper()
        mom_signal = row["Momentum_Signal"]

        is_index = False
        if raw_ticker in ["SPX", "RUT", "NDX", "VIX"]:
            ticker_symbol = f"^{raw_ticker}" if not raw_ticker.startswith("^") else raw_ticker
            is_index = True
        else:
            ticker_symbol = raw_ticker
            if raw_ticker.startswith("^"):
                is_index = True

        print(f"\n" + "=" * 60)
        print(f"🔄 Processing Pipeline: {ticker_symbol} | Momentum: {mom_signal}")
        print("=" * 60)

        try:
            ticker = yf.Ticker(ticker_symbol)
            sector = ticker.info.get("sector", "Index/ETF")  # <-- NEW LINE
            try:
                spot_price = ticker.history(period="1d")["Close"].iloc[-1]
            except (IndexError, KeyError, TypeError):
                spot_price = ticker.info.get("regularMarketPrice", ticker.info.get("previousClose"))

            if not spot_price or pd.isna(spot_price):
                print(f"⚠️ Skipping {ticker_symbol}: Unable to resolve valid spot price.")
                continue

            expirations = ticker.options
            if not expirations:
                print(f"⚠️ Skipping {ticker_symbol}: No option chains available.")
                continue

            # --- DYNAMIC MULTI-TIMEFRAME TARGETING ---
            # Indexes get 0 DTE, 7 DTE, and 28 DTE. Equities get 7 DTE and 28 DTE.
            target_buckets = [0, 7, 28] if is_index else [7, 28]
            today = pd.Timestamp.today().normalize()

            selected_expirations = {}

            for bucket in target_buckets:
                best_diff = float('inf')
                best_exp = expirations[0]
                actual_dte_for_best = 0

                for exp in expirations:
                    exp_date = pd.to_datetime(exp)
                    days_out = (exp_date - today).days

                    # Ensure we don't accidentally pick a negative DTE if data is stale
                    if days_out < 0:
                        continue

                    if abs(days_out - bucket) < best_diff:
                        best_diff = abs(days_out - bucket)
                        best_exp = exp
                        actual_dte_for_best = days_out

                # Prevent analyzing the exact same chain twice if buckets overlap
                if best_exp not in [v[0] for v in selected_expirations.values()]:
                    selected_expirations[f"~{bucket} DTE"] = (best_exp, actual_dte_for_best)

            for bucket_label, (target_expiry, actual_dte) in selected_expirations.items():
                print(f"   ⏱️ Evaluating {bucket_label} -> Expiry: {target_expiry} ({actual_dte} DTE)")
                t = max(float(actual_dte), 0.5) / 365.0
                r = 0.045
                opt_chain = ticker.option_chain(target_expiry)
                calls = opt_chain.calls.dropna(subset=["strike", "openInterest", "impliedVolatility"])
                puts = opt_chain.puts.dropna(subset=["strike", "openInterest", "impliedVolatility"])

                call_data = []
                for _, opt_row in calls.iterrows():
                    k, oi, iv = opt_row["strike"], opt_row["openInterest"], opt_row["impliedVolatility"]
                    if iv < 0.01: continue
                    gamma = bs_gamma(spot_price, k, t, r, iv)
                    dollar_gex = gamma * oi * 100 * spot_price
                    call_data.append({"strike": k, "Call_GEX": dollar_gex / 1_000_000, "Call_OI": oi})

                put_data = []
                for _, opt_row in puts.iterrows():
                    k, oi, iv = opt_row["strike"], opt_row["openInterest"], opt_row["impliedVolatility"]
                    if iv < 0.01: continue
                    gamma = bs_gamma(spot_price, k, t, r, iv)
                    dollar_gex = -gamma * oi * 100 * spot_price
                    put_data.append({"strike": k, "Put_GEX": dollar_gex / 1_000_000, "Put_OI": oi})

                df_calls = pd.DataFrame(call_data)
                df_puts = pd.DataFrame(put_data)

                if df_calls.empty or df_puts.empty:
                    print(f"      ⚠️ Insufficient options liquidity data for {target_expiry}.")
                    continue

                combined = pd.merge(df_calls, df_puts, on="strike", how="outer").fillna(0)
                combined["Net_GEX_Millions"] = combined["Call_GEX"] + combined["Put_GEX"]

                call_wall_strike = combined.loc[combined["Call_OI"].idxmax(), "strike"]
                put_wall_strike = combined.loc[combined["Put_OI"].idxmax(), "strike"]

                combined["dist_to_spot"] = abs(combined["strike"] - spot_price)
                local_zone = combined[combined["dist_to_spot"] < (spot_price * 0.06)].sort_values("strike")

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
                rationale = "Momentum signal does not align cleanly with structural walls." # noqa

                if mom_signal == "Bullish" and spot_price > put_wall_strike:
                    valid_puts = combined[combined["strike"] <= put_wall_strike].sort_values("strike", ascending=False)
                    if not valid_puts.empty:
                        short_p = valid_puts.iloc[0]["strike"]
                        strategy = "Bull Put Credit Spread"
                        targets = f"Short Put: ${short_p:,.2f}" # noqa
                        rationale = "Bullish momentum supported by Put Wall dealer floor." # noqa

                elif mom_signal == "Bearish" and spot_price < call_wall_strike:
                    valid_calls = combined[combined["strike"] >= call_wall_strike].sort_values("strike")
                    if not valid_calls.empty:
                        short_c = valid_calls.iloc[0]["strike"]
                        strategy = "Bear Call Credit Spread"
                        targets = f"Short Call: ${short_c:,.2f}" # noqa
                        rationale = "Bearish momentum capped by Call Wall dealer ceiling." # noqa

                elif mom_signal == "Breakout" and spot_price >= call_wall_strike * 0.985:
                    breakout_c = combined[combined["strike"] >= call_wall_strike].sort_values("strike")
                    if not breakout_c.empty:
                        target_c = breakout_c.iloc[0]["strike"]
                    if regime == "NEGATIVE GAMMA":
                        strategy = "High-Conviction Gamma Squeeze"
                        targets = f"Buy Strike: ${target_c:,.2f}" # noqa
                        rationale = "Breakout near Call Wall fueled by Negative Gamma dealer buying." # noqa
                    else:
                        strategy = "Stand Aside (Positive Gamma Pin)"
                        targets = "N/A"
                        rationale = "Spot approaching Call Wall but Positive Gamma will likely cap the move." # noqa

                # --- NEW CONVICTION LOGIC ---
                conviction = "Standard"
                if strategy != "Stand Aside (Conflicting Signals)":
                    if mom_signal == "Breakout":
                        conviction = "High"
                    # Bullish and spot is within 2% of the put wall support
                    elif mom_signal == "Bullish" and spot_price <= put_wall_strike * 1.02:
                        conviction = "High"
                    # Bearish and spot is within 2% of the call wall resistance
                    elif mom_signal == "Bearish" and spot_price >= call_wall_strike * 0.98:
                        conviction = "High"

                master_results.append({
                    "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "Ticker": ticker_symbol,
                    "Sector": sector,                # <-- NEW COLUMN
                    "Conviction": conviction,        # <-- NEW COLUMN
                    "Timeframe": bucket_label,
                    "Momentum_Signal": mom_signal,
                    "Market_Regime": regime,
                    "Spot_Price": spot_price,
                    "Target_Expiry": target_expiry,
                    "Actual_DTE": actual_dte,
                    "Call_Wall_Ceiling": call_wall_strike,
                    "Put_Wall_Floor": put_wall_strike,
                    "Gamma_Flip": flip_strike,
                    "Confirmed_Strategy": strategy,
                    "Target_Strikes": targets,
                })

                print(f"      ✅ Verified -> {strategy} | Target: {targets}")
                print(f"         Levels -> Floor (Put Wall): ${put_wall_strike:,.2f} |" # noqa 
                      f" Ceiling (Call Wall): ${call_wall_strike:,.2f}") # noqa

        except Exception as e:
            print(f"❌ Error processing {ticker_symbol}: {e}")

    if master_results:
        master_df = pd.DataFrame(master_results)
        # Sort so the spreadsheet groups by Ticker first, then by Timeframe (DTE) ascending
        master_df = master_df.sort_values(by=["Ticker", "Actual_DTE"])
        master_filename = "unified_gex_momentum_master_log.csv"
        master_df.to_csv(master_filename, index=False)
        print("\n" + "=" * 60)
        print(f"💾 Master Pipeline Log Saved Successfully: {master_filename}")
        print("=" * 60)

        # --- NEW CODE: PUSH TO SHEETS AND SEND EMAIL ---
        export_gex_to_sheets(master_df)
        send_email_gex_report(master_df)


if __name__ == "__main__":
    process_pipeline_batch("momentum_suite/momentum_signals.csv")
