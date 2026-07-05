"""テクニカル指標の計算"""
from __future__ import annotations

import pandas as pd


def compute_technical(prices: pd.DataFrame) -> dict | None:
    """1銘柄の株価履歴からテクニカル指標を計算する。

    prices: index=date, columns=[open, high, low, close, volume]
    戻り値: 指標のdict。データ不足なら None
    """
    if prices is None or len(prices) < 200:
        return None

    close = prices["close"]
    volume = prices["volume"]
    last = close.iloc[-1]

    sma50 = close.rolling(50).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1]

    # RSI(14) - Wilder方式
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = (100 - 100 / (1 + rs)).iloc[-1]

    # MACD(12,26,9) ヒストグラム。株価水準に依存しないよう終値で正規化する
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist_norm = float((macd - signal).iloc[-1] / last)

    ret_63d = float(last / close.iloc[-63] - 1) if len(close) >= 63 else None

    vol20 = volume.rolling(20).mean().iloc[-1]
    vol60 = volume.rolling(60).mean().iloc[-1]
    volume_trend = float(vol20 / vol60 - 1) if vol60 and vol60 > 0 else None

    return {
        "price": float(last),
        "sma50_ratio": float(last / sma50 - 1) if sma50 else None,
        "sma200_ratio": float(last / sma200 - 1) if sma200 else None,
        "rsi": float(rsi) if pd.notna(rsi) else None,
        "macd_hist": macd_hist_norm,
        "ret_63d": ret_63d,
        "volume_trend": volume_trend,
    }
