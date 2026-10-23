import numpy as np
import pandas as pd
import yfinance as yf


def fetch_historical_data(ticker, period="1y"):
    """Fetches daily OHLCV data for a given ticker."""
    stock = yf.Ticker(ticker)
    df = stock.history(period=period)
    return df


def detect_vcp_pattern(df, min_contractions=2, max_contractions=4):
    """Programmatically detects Volatility Contraction Patterns (VCP)."""
    if df.empty or len(df) < 100:
        return {"vcp_detected": False, "reason": "Insufficient data"}

    # Calculate 50-day and 200-day Moving Averages for trend filtering
    df["SMA_50"] = df["Close"].rolling(window=50).mean()
    df["SMA_200"] = df["Close"].rolling(window=200).mean()

    current_close = df["Close"].iloc[-1]
    sma_50 = df["SMA_50"].iloc[-1]
    sma_200 = df["SMA_200"].iloc[-1]

    # Rule 1: Macro Trend Filter (Price should generally be above key moving averages)
    if current_close < sma_50 or sma_50 < sma_200:
        return {
            "vcp_detected": False,
            "reason": "Failed macro trend filter (Price below 50/200 SMA)",
        }

    # Isolate the most recent 120 trading days (~6 months) to look for a base
    recent_df = df.tail(120).copy()

    # Identify local swing highs and lows to measure pullbacks
    # We find peaks (highs) and troughs (lows) across the lookback window
    window = 10
    recent_df["Rolling_Max"] = (
        recent_df["High"].shift(-window).rolling(window * 2, center=True).max()
    )
    recent_df["Is_Peak"] = recent_df["High"] == recent_df["Rolling_Max"]

    peaks = recent_df[recent_df["Is_Peak"]].copy()

    if len(peaks) < min_contractions + 1:
        return {
            "vcp_detected": False,
            "reason": "Not enough structural price swings found",
        }

    # Extract the last few major swing highs and measure the depth of pullbacks following them
    contractions = []
    peak_prices = peaks["High"].values

    for i in range(len(peak_prices) - 1):
        p1 = peak_prices[i]
        # Find the lowest low between peak i and peak i+1
        subset = recent_df.loc[peaks.index[i] : peaks.index[i + 1]]
        if subset.empty:
            continue
        trough = subset["Low"].min()
        pullback_pct = ((p1 - trough) / p1) * 100
        contractions.append(pullback_pct)

    # Keep only the last N contractions leading up to the current price
    recent_contractions = contractions[-max_contractions:]

    if len(recent_contractions) < min_contractions:
        return {
            "vcp_detected": False,
            "reason": "Not enough contractions detected",
        }

    # Rule 2: Check if contractions are progressively tightening (e.g., 20% -> 10% -> 5%)
    is_tightening = all(
        recent_contractions[i] > recent_contractions[i + 1]
        for i in range(len(recent_contractions) - 1)
    )

    # Rule 3: Volume drying up near the right side of the base
    avg_vol_base = recent_df["Volume"].mean()
    recent_vol = recent_df["Volume"].tail(5).mean()
    volume_drying_up = recent_vol < avg_vol_base

    # Rule 4: Proximity to pivot (current price is near the top of the latest contraction peak)
    latest_peak = peak_prices[-1]
    distance_from_pivot = ((latest_peak - current_close) / latest_peak) * 100
    near_pivot = abs(distance_from_pivot) <= 5.0  # Within 5% of breakout level

    vcp_valid = is_tightening and near_pivot

    return {
        "vcp_detected": vcp_valid,
        "contractions_depth_pct": [round(c, 2) for c in recent_contractions],
        "is_tightening": is_tightening,
        "distance_from_pivot_pct": round(distance_from_pivot, 2),
        "volume_drying_up": volume_drying_up,
        "current_price": current_close,
        "pivot_resistance": round(latest_peak, 2),
    }


# --- Example Execution ---
if __name__ == "__main__":
    ticker_symbol = "UNH"
    data = fetch_historical_data(ticker_symbol)
    result = detect_vcp_pattern(data)

    print(f"=== VCP Scan Results for {ticker_symbol} ===")
    for key, val in result.items():
        print(f"{key}: {val}")
