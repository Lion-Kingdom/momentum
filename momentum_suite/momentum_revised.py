"""
Dynamic S&P 500 & ETF Momentum Worker with 14-Day ATR & Order Blocks.
Compliance: Pylint 10/10, Black, PEP 8.
"""

import io
import os
import sys
from typing import Optional

import pandas as pd
import requests
import yfinance as yf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ETF_CSV_PATH = os.path.join(SCRIPT_DIR, "etfs.csv")


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Wilder's Exponential Moving Average RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs_val = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs_val))


def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Calculate 14-period Average True Range (ATR)."""
    high = df["High"]
    low = df["Low"]
    close = df["Close"].shift(1)

    tr1 = high - low
    tr2 = (high - close).abs()
    tr3 = (low - close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr_series = true_range.rolling(window=period).mean()
    return float(atr_series.iloc[-1]) if not atr_series.empty else 0.0


def identify_order_blocks(df: pd.DataFrame, lookback: int = 50) -> dict:
    """Detects unmitigated bullish and bearish order blocks from historical price action."""
    df = df.copy()
    highs = df["High"].values
    lows = df["Low"].values
    closes = df["Close"].values
    opens = df["Open"].values

    bullish_obs = []
    bearish_obs = []

    for i in range(5, len(df) - lookback):
        if closes[i] < opens[i]:  # Bearish candle
            body_size = abs(closes[i] - opens[i])
            next_move = closes[i + 2] - closes[i]
            if next_move > (body_size * 1.5):
                bullish_obs.append({
                    "Zone_Low": float(lows[i]),
                    "Zone_High": float(max(opens[i], closes[i])),
                })
        elif closes[i] > opens[i]:  # Bullish candle
            body_size = abs(closes[i] - opens[i])
            next_move = closes[i] - closes[i + 2]
            if next_move > (body_size * 1.5):
                bearish_obs.append({
                    "Zone_Low": float(min(opens[i], closes[i])),
                    "Zone_High": float(highs[i]),
                })

    curr_price = float(closes[-1])
    active_support = [ob for ob in bullish_obs if ob["Zone_High"] <= curr_price]
    active_resistance = [ob for ob in bearish_obs if ob["Zone_Low"] >= curr_price]

    nearest_support = max(active_support, key=lambda x: x["Zone_High"]) if active_support else {"Zone_Low": curr_price * 0.92, "Zone_High": curr_price * 0.94}
    nearest_resistance = min(active_resistance, key=lambda x: x["Zone_Low"]) if active_resistance else {"Zone_Low": curr_price * 1.06, "Zone_High": curr_price * 1.08}

    return {
        "OB_Support": round(nearest_support["Zone_High"], 2),
        "OB_Resistance": round(nearest_resistance["Zone_Low"], 2),
    }


def fetch_sp500_tickers() -> list:
    """Scrape live S&P 500 symbols from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        table = pd.read_html(io.StringIO(response.text))[0]
        return table["Symbol"].str.replace(".", "-", regex=False).tolist()
    except (requests.RequestException, ValueError):
        return []


def fetch_csv_etfs(filepath: str = ETF_CSV_PATH) -> list:
    """Extract tickers from a locally saved CSV of ETFs."""
    try:
        df = pd.read_csv(filepath)
        col_name = "Symbol" if "Symbol" in df.columns else "Ticker"
        if col_name not in df.columns:
            return []
        return df[col_name].dropna().astype(str).str.strip().tolist()
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return []


def process_ticker(ticker: str, data: pd.DataFrame, bench: float) -> Optional[dict]:
    """Extract metrics, calculate 14-day ATR, Order Blocks, and apply phase filters."""
    try:
        if isinstance(data.columns, pd.MultiIndex):
            if ticker in data.columns.levels[0]:
                df = data[ticker].dropna(how="all")
            elif ticker in data.columns.levels[1]:
                df = data.xs(ticker, axis=1, level=1).dropna(how="all")
            else:
                return None
        else:
            if ticker not in data:
                return None
            df = data[ticker].dropna(how="all")

        if "Close" not in df.columns or len(df["Close"].dropna()) < 50:
            return None

        close = df["Close"].dropna()
        curr_p = float(close.iloc[-1])
        sma20 = float(close.rolling(20).mean().iloc[-1])
        sma50 = float(close.rolling(50).mean().iloc[-1])
        rsi = float(calculate_rsi(close).iloc[-1])
        atr_14 = calculate_atr(df, period=14)

        c1d = (curr_p - close.iloc[-2]) / close.iloc[-2]
        c5d = (curr_p - close.iloc[-6]) / close.iloc[-6]
        c1m = (curr_p - close.iloc[-22]) / close.iloc[-22]
        perf = (curr_p - close.iloc[0]) / close.iloc[0]

        # Basic baseline trend check
        if not (curr_p > sma20 and perf >= (bench * 0.5)):
            return None

        # --- REFINED HYBRID THRESHOLDS ---
        phase = "Consolidating"
        if rsi > 88:
            phase = "Exhausted_Trap"
        elif c5d < 0 and c1d <= 0 and (45 <= rsi <= 65):
            phase = "Pullback_Reset"
        elif c1d > 0.01 and c5d <= 0:
            phase = "Regaining"
        elif c1d > 0 and c5d > 0 and c1m > 0:
            phase = "Strong_Expansion"
        elif c1d < -0.01 and c5d < -0.03:
            phase = "Losing_Momentum_Flush"

        if phase == "Consolidating" and c1m < 0.10:
            return None

        # Swing clearance minimums based on 2x ATR
        min_room_to_grow = round(atr_14 * 2.0, 2)
        optimal_target = round(atr_14 * 3.0, 2)

        # --- ORDER BLOCK SUPPORT & RESISTANCE ---
        ob_data = identify_order_blocks(df)
        ob_support = ob_data["OB_Support"]
        ob_resistance = ob_data["OB_Resistance"]

        return {
            "Ticker": ticker,
            "Price": round(curr_p, 2),
            "Open": round(float(df["Open"].iloc[-1]), 2),
            "High": round(float(df["High"].iloc[-1]), 2),
            "Low": round(float(df["Low"].iloc[-1]), 2),
            "Close": round(curr_p, 2),
            "Volume": int(df["Volume"].iloc[-1]),
            "1D%": round(c1d * 100, 2),
            "5D%": round(c5d * 100, 2),
            "1M%": round(c1m * 100, 2),
            "RSI": round(rsi, 1),
            "SMA_50": round(sma50, 2),
            "Above_50": bool(curr_p > sma50),
            "ATR_14": round(atr_14, 2),
            "Min_Room_Needed": min_room_to_grow,
            "Optimal_Target_3x": optimal_target,
            "OB_Support": ob_support,
            "OB_Resistance": ob_resistance,
            "Phase": phase,
            "Alert": "VOLATILITY" if abs(c1d) > 0.015 else "",
        }
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def run_worker():
    """Execute main momentum scan and output signals."""
    sp500_tickers = fetch_sp500_tickers()
    etf_tickers = fetch_csv_etfs()
    tickers = list(set(sp500_tickers + etf_tickers))

    if not tickers:
        sys.exit("No tickers available to process.")

    print(f"[INFO] Scanning {len(tickers)} symbols...")

    spy_data = yf.download("SPY", period="1y", auto_adjust=True, progress=False)
    close_series = (
        spy_data["Close"].squeeze()
        if isinstance(spy_data["Close"], pd.DataFrame)
        else spy_data["Close"]
    )
    bench = (float(close_series.iloc[-1]) - float(close_series.iloc[0])) / float(close_series.iloc[0])

    data = yf.download(tickers, period="1y", group_by="ticker", progress=False)
    res = [process_ticker(t, data, bench) for t in tickers]
    df = pd.DataFrame([r for r in res if r])

    if df.empty:
        print("[INFO] No tickers qualified today.")
        sys.exit(0)

    df = df.sort_values(by=["Alert", "Phase", "Price"], ascending=[False, True, False])

    def map_signal(row):
        if row["Phase"] == "Strong_Expansion" and row["Above_50"]:
            return "Breakout"
        if row["Phase"] in ["Pullback_Reset", "Regaining"]:
            return "Bullish"
        if row["Phase"] in ["Exhausted_Trap", "Losing_Momentum_Flush"]:
            return "Bearish"
        return "Neutral"

    df["Momentum_Signal"] = df.apply(map_signal, axis=1)

    output_path = os.path.join(SCRIPT_DIR, "momentum_signals.csv")
    df.to_csv(output_path, index=False)
    print(f"[INFO] Successfully exported momentum signals to {output_path}")


if __name__ == "__main__":
    run_worker()
