"""複合スコアリング: 各指標を市場内で偏差値化して重み付き合算する"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .indicators import compute_technical
from .store import Store

logger = logging.getLogger(__name__)

# カテゴリ → (指標列, 符号)。符号 -1 は「小さいほど良い」指標
CATEGORY_METRICS: dict[str, list[tuple[str, int]]] = {
    "value": [("trailingPE", -1), ("priceToBook", -1)],
    "quality": [("returnOnEquity", 1), ("operatingMargins", 1)],
    "growth": [("revenueGrowth", 1), ("earningsGrowth", 1)],
    "trend": [("sma50_ratio", 1), ("sma200_ratio", 1)],
    "momentum": [("ret_63d", 1), ("macd_hist", 1)],
    "volume": [("volume_trend", 1)],
}


def _zscore(series: pd.Series) -> pd.Series:
    """外れ値の影響を抑えるため上下1%でウィンザライズしてからzスコア化。欠損は0(中立)扱い"""
    s = series.astype(float)
    valid = s.dropna()
    if len(valid) < 5 or valid.std(ddof=0) == 0:
        return pd.Series(0.0, index=series.index)
    lo, hi = valid.quantile(0.01), valid.quantile(0.99)
    s = s.clip(lo, hi)
    z = (s - s.mean()) / s.std(ddof=0)
    return z.fillna(0.0).clip(-3, 3)


def build_features(store: Store, dataset: pd.DataFrame) -> pd.DataFrame:
    """財務データにテクニカル指標を結合した特徴量テーブルを作る"""
    tech_rows = []
    for t in dataset["ticker"]:
        tech = compute_technical(store.load_prices(t))
        tech_rows.append({"ticker": t, **(tech or {})})
    df = dataset.merge(pd.DataFrame(tech_rows), on="ticker", how="left")

    # 赤字企業のPER(負値)は「割安」ではないので欠損扱いにする
    df.loc[df["trailingPE"] <= 0, "trailingPE"] = np.nan
    df.loc[df["priceToBook"] <= 0, "priceToBook"] = np.nan

    # 株価データが無い銘柄は選定対象外
    before = len(df)
    df = df.dropna(subset=["price"]).reset_index(drop=True)
    if before - len(df):
        logger.info("株価データ不足のため %d銘柄を除外", before - len(df))
    return df


def score(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """特徴量テーブルにカテゴリ別スコアと総合スコアを付与し、ランキングを返す"""
    weights: dict[str, float] = cfg["weights"]

    for cat, metrics in CATEGORY_METRICS.items():
        zs = []
        for col, sign in metrics:
            # 市場(JP/US)ごとに正規化して分布の違いを吸収する
            z = df.groupby("market")[col].transform(_zscore) * sign
            zs.append(z)
        df[f"z_{cat}"] = pd.concat(zs, axis=1).mean(axis=1)

    df["composite"] = sum(df[f"z_{cat}"] * w for cat, w in weights.items())

    # RSI過熱ペナルティ
    overbought = df["rsi"] > cfg["rsi_overbought"]
    df.loc[overbought, "composite"] -= cfg["rsi_penalty"] * sum(weights.values())

    # 表示用に偏差値 (50 + 10z) へ変換
    comp = df["composite"]
    df["score"] = 50 + 10 * (comp - comp.mean()) / comp.std(ddof=0)

    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1
    return df
