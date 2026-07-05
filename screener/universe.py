"""銘柄ユニバースの取得 (日経225 + S&P500)"""
from __future__ import annotations

import io
import logging
import re

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) stock-screener/1.0"}

NIKKEI_COMPONENT_URL = "https://indexes.nikkei.co.jp/nkave/index/component?idx=nk225"
SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# 日経225ページのスクレイピングに失敗した場合の予備リスト (主要大型株)
FALLBACK_JP_CODES = [
    "7203", "6758", "9984", "8306", "6861", "9432", "8035", "4063", "6098",
    "9433", "8316", "4519", "6501", "6902", "7974", "9983", "8058", "8001",
    "8031", "2914", "4568", "6367", "6594", "6981", "7741", "4502", "6273",
    "8411", "3382", "9022", "9020", "5108", "4661", "6178", "8766", "8750",
    "7267", "7201", "6752", "6503", "8802", "8801", "4452", "4911", "2802",
    "6954", "7751", "6971", "8591", "9101", "9104", "9107", "5401", "1605",
]


def get_japan_tickers(limit: int | None = None) -> pd.DataFrame:
    """日経225構成銘柄を取得する。戻り値: columns=[ticker, name, market]"""
    codes: list[tuple[str, str]] = []
    try:
        resp = requests.get(NIKKEI_COMPONENT_URL, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        # ページ内の「4桁コード + 銘柄名」を抽出する
        matches = re.findall(
            r'class="component-list-code[^"]*"[^>]*>\s*(\d{4})\s*<'
            r'|<div class="component-list-name[^"]*"[^>]*>([^<]+)<',
            resp.text,
        )
        pending_code = None
        for code, name in matches:
            if code:
                pending_code = code
            elif pending_code and name:
                codes.append((pending_code, name.strip()))
                pending_code = None
        if not codes:
            # HTML構造が変わった場合に備え、テーブルからの読み取りも試す
            tables = pd.read_html(io.StringIO(resp.text))
            for t in tables:
                cols = [str(c) for c in t.columns]
                if any("コード" in c for c in cols):
                    code_col = next(c for c in t.columns if "コード" in str(c))
                    name_col = next((c for c in t.columns if "社名" in str(c) or "銘柄" in str(c)), None)
                    for _, row in t.iterrows():
                        code = re.match(r"\d{4}", str(row[code_col]))
                        if code:
                            codes.append((code.group(), str(row[name_col]) if name_col else ""))
    except Exception as e:  # noqa: BLE001
        logger.warning("日経225リストの取得に失敗: %s", e)

    if len(codes) < 100:
        logger.warning("日経225の取得結果が不完全 (%d件)。予備リスト(主要%d銘柄)を使用します",
                       len(codes), len(FALLBACK_JP_CODES))
        codes = [(c, "") for c in FALLBACK_JP_CODES]

    df = pd.DataFrame(codes, columns=["code", "name"]).drop_duplicates("code")
    df["ticker"] = df["code"] + ".T"
    df["market"] = "JP"
    df = df[["ticker", "name", "market"]]
    if limit:
        df = df.head(limit)
    return df.reset_index(drop=True)


def get_us_tickers(limit: int | None = None) -> pd.DataFrame:
    """S&P500構成銘柄をWikipediaから取得する。戻り値: columns=[ticker, name, market]"""
    resp = requests.get(SP500_WIKI_URL, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    table = next(t for t in tables if "Symbol" in t.columns)
    df = pd.DataFrame({
        # BRK.B → BRK-B のように yfinance 形式へ変換
        "ticker": table["Symbol"].astype(str).str.replace(".", "-", regex=False),
        "name": table["Security"].astype(str),
    })
    df["market"] = "US"
    df = df.drop_duplicates("ticker")
    if limit:
        df = df.head(limit)
    return df.reset_index(drop=True)


def get_universe(limit: int | None = None) -> pd.DataFrame:
    jp = get_japan_tickers(limit)
    us = get_us_tickers(limit)
    logger.info("ユニバース: 日本株 %d銘柄, 米国株 %d銘柄", len(jp), len(us))
    return pd.concat([jp, us], ignore_index=True)
