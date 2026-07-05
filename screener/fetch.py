"""yfinance によるデータ取得 (SQLiteキャッシュ付き)"""
from __future__ import annotations

import logging
import time

import pandas as pd
import yfinance as yf

from .store import Store

logger = logging.getLogger(__name__)

PRICE_BATCH_SIZE = 50
FUNDAMENTAL_SLEEP = 0.3  # レート制限対策のウェイト(秒)

# .info から抜き出すキー
FUNDAMENTAL_KEYS = [
    "trailingPE", "priceToBook", "returnOnEquity", "operatingMargins",
    "revenueGrowth", "earningsGrowth", "marketCap", "shortName",
    "dividendYield",  # yfinance 0.2.54以降は%表記 (3.5 = 3.5%)
]


def fetch_prices(store: Store, tickers: list[str], period: str,
                 cache_days: float) -> None:
    """株価履歴をバッチ取得してキャッシュに保存する"""
    fresh = store.fresh_price_tickers(cache_days)
    targets = [t for t in tickers if t not in fresh]
    logger.info("株価取得: %d銘柄 (キャッシュ済 %d銘柄)", len(targets), len(tickers) - len(targets))

    for i in range(0, len(targets), PRICE_BATCH_SIZE):
        batch = targets[i:i + PRICE_BATCH_SIZE]
        logger.info("  株価バッチ %d-%d / %d", i + 1, i + len(batch), len(targets))
        try:
            data = yf.download(batch, period=period, group_by="ticker",
                               auto_adjust=True, threads=True, progress=False)
        except Exception as e:  # noqa: BLE001
            logger.warning("  バッチ取得失敗、スキップ: %s", e)
            continue
        for t in batch:
            try:
                df = data[t] if len(batch) > 1 else data
                df = df.dropna(subset=["Close"])
                if len(df) >= 60:  # 最低限のデータ量がある銘柄のみ保存
                    store.save_prices(t, df)
                else:
                    logger.debug("  %s: データ不足 (%d日分)", t, len(df))
            except (KeyError, TypeError):
                logger.debug("  %s: 株価データなし", t)
        time.sleep(1)


def fetch_fundamentals(store: Store, tickers: list[str], cache_days: float) -> None:
    """財務指標を1銘柄ずつ取得してキャッシュに保存する"""
    fresh = store.fresh_fundamental_tickers(cache_days)
    targets = [t for t in tickers if t not in fresh]
    logger.info("財務データ取得: %d銘柄 (キャッシュ済 %d銘柄)", len(targets), len(tickers) - len(targets))

    for n, t in enumerate(targets, 1):
        if n % 25 == 0:
            logger.info("  財務データ %d / %d", n, len(targets))
        try:
            info = yf.Ticker(t).info
            data = {k: info.get(k) for k in FUNDAMENTAL_KEYS}
            store.save_fundamentals(t, data)
        except Exception as e:  # noqa: BLE001
            logger.warning("  %s: 財務データ取得失敗: %s", t, e)
            store.save_fundamentals(t, {})  # 失敗も記録して再試行の嵐を防ぐ
        time.sleep(FUNDAMENTAL_SLEEP)


def load_dataset(store: Store, universe: pd.DataFrame) -> pd.DataFrame:
    """キャッシュから全銘柄の財務データを読み込み、ユニバースに結合する"""
    records = []
    for _, row in universe.iterrows():
        f = store.load_fundamentals(row["ticker"]) or {}
        records.append({"ticker": row["ticker"], **{k: f.get(k) for k in FUNDAMENTAL_KEYS}})
    df = universe.merge(pd.DataFrame(records), on="ticker", how="left")
    # Wikipediaや日経ページ由来の名前が無ければ yfinance の shortName を使う
    df["name"] = df["name"].where(df["name"].astype(bool), df["shortName"])
    return df.drop(columns=["shortName"])
