"""バックテスト: スクリーナーの選定ルールを過去データで検証する

無料データの制約に基づき、2つの変種で検証する:
- テクニカルのみ: 各リバランス日時点の株価データだけで計算(先読みバイアス無し)
- 複合: 財務指標を「現在値で固定」して複合スコア化(楽観バイアスあり・参考値)

共通の限界:
- ユニバースは現在の構成銘柄 → 生存者バイアスあり(結果は上振れしやすい)
- 取引コスト・税・為替は未考慮。日米とも現地通貨建てリターンを等ウェイト合算
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
import yfinance as yf

from . import fetch, scoring, universe
from .indicators import compute_technical
from .store import Store

logger = logging.getLogger(__name__)

INDEX_TICKERS = {"^N225": "日経225", "^GSPC": "S&P500"}
TECH_CATEGORIES = ("trend", "momentum", "volume")
STALE_DAYS = 10          # リバランス日の直近にデータが無い銘柄は除外
HISTORY_CACHE_DAYS = 7   # バックテスト用株価キャッシュの有効日数
MONTHS_PER_YEAR = 12


def _tech_weights(weights: dict[str, float]) -> dict[str, float]:
    """テクニカル系カテゴリのみ残して合計1.0に再正規化する"""
    w = {k: v for k, v in weights.items() if k in TECH_CATEGORIES}
    total = sum(w.values())
    return {k: v / total for k, v in w.items()}


def _fetch_indices(store: Store, years: int) -> None:
    """参考指数を個別取得する。バッチ取得(yf.download)だと指数は直近数年しか返らないため"""
    start = date.today() - timedelta(days=365 * (years + 1) + 30)
    for t in INDEX_TICKERS:
        existing = store.load_prices(t)
        if not existing.empty and existing.index[0] <= pd.Timestamp(start) + pd.Timedelta(days=14):
            continue
        try:
            df = yf.Ticker(t).history(start=start.isoformat(), auto_adjust=True)
            df.index = df.index.tz_localize(None)
            df = df.dropna(subset=["Close"])
            if len(df) >= 60:
                store.save_prices(t, df)
                logger.info("参考指数 %s: %d日分取得", t, len(df))
        except Exception as e:  # noqa: BLE001
            logger.warning("参考指数 %s の取得失敗: %s", t, e)


def _features_at(day: pd.Timestamp, prices: dict[str, pd.DataFrame],
                 base: pd.DataFrame) -> pd.DataFrame:
    """リバランス日時点のテクニカル指標を計算し、財務データと結合する"""
    rows = []
    for _, r in base.iterrows():
        p = prices.get(r["ticker"])
        if p is None:
            continue
        hist = p.loc[:day]
        if len(hist) < 200 or (day - hist.index[-1]).days > STALE_DAYS:
            continue
        tech = compute_technical(hist)
        if tech:
            rows.append({**r.to_dict(), **tech})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # 赤字企業のPER(負値)は「割安」ではないので欠損扱い (build_features と同じ処理)
    df.loc[df["trailingPE"] <= 0, "trailingPE"] = np.nan
    df.loc[df["priceToBook"] <= 0, "priceToBook"] = np.nan
    return df


def _metrics(rets: pd.Series) -> dict[str, float]:
    """月次リターン系列から成績指標を計算する"""
    cum = (1 + rets).cumprod()
    years = len(rets) / MONTHS_PER_YEAR
    vol = rets.std(ddof=0) * np.sqrt(MONTHS_PER_YEAR)
    return {
        "total": cum.iloc[-1] - 1,
        "cagr": cum.iloc[-1] ** (1 / years) - 1,
        "vol": vol,
        "sharpe": rets.mean() * MONTHS_PER_YEAR / vol if vol > 0 else np.nan,
        "maxdd": (cum / cum.cummax() - 1).min(),
    }


def _yearly(rets: pd.Series) -> pd.Series:
    return rets.groupby(rets.index.year).apply(lambda r: (1 + r).prod() - 1)


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%" if pd.notna(x) else "—"


def run_backtest(prices: dict[str, pd.DataFrame], dataset: pd.DataFrame,
                 idx_prices: dict[str, pd.DataFrame], scoring_cfg: dict,
                 years: int, top_n: int) -> dict:
    """月末リバランスでトップN等ウェイト保有を繰り返し、月次リターン系列を作る"""
    wide = pd.DataFrame({t: p["close"] for t, p in prices.items()})
    wide = wide.sort_index().ffill(limit=7)
    month_close = wide.resample("ME").last()
    period_ret = month_close.pct_change()

    n_periods = min(years * MONTHS_PER_YEAR, len(month_close) - 13)
    dates = month_close.index
    rebal_dates = dates[-(n_periods + 1):-1]  # 最後の月末は評価のみに使う

    weight_sets = {
        "テクニカルのみ": _tech_weights(scoring_cfg["weights"]),
        "複合(財務は現在値固定)": dict(scoring_cfg["weights"]),
    }
    strat_rets: dict[str, dict] = {name: {} for name in weight_sets}
    bench_rets: dict = {}
    last_picks: dict[str, list[str]] = {}

    for i, d in enumerate(rebal_dates, 1):
        feats = _features_at(d, prices, dataset)
        if feats.empty:
            continue
        next_d = dates[dates.get_loc(d) + 1]
        for name, w in weight_sets.items():
            ranked = scoring.score(feats.copy(), {**scoring_cfg, "weights": w})
            picks = ranked.head(top_n)["ticker"].tolist()
            strat_rets[name][next_d] = period_ret.loc[next_d, picks].dropna().mean()
            last_picks[name] = picks
        # ベンチマーク: その時点で投資可能だった全銘柄の等ウェイト平均
        investable = list(feats["ticker"])
        bench_rets[next_d] = period_ret.loc[next_d, investable].dropna().mean()
        if i % 12 == 0:
            logger.info("  バックテスト進捗 %d / %d ヶ月", i, len(rebal_dates))

    series = {name: pd.Series(r).sort_index() for name, r in strat_rets.items()}
    series["ベンチマーク(全銘柄等ウェイト)"] = pd.Series(bench_rets).sort_index()

    # 参考指数 (現地通貨建て)
    ref_index = series["ベンチマーク(全銘柄等ウェイト)"].index
    for t, label in INDEX_TICKERS.items():
        p = idx_prices.get(t)
        if p is None or p.empty:
            continue
        m = p["close"].resample("ME").last().pct_change()
        series[f"参考: {label}"] = m.reindex(ref_index)

    return {"series": series, "last_picks": last_picks,
            "n_universe": len(dataset), "rebal_dates": rebal_dates}


def build_report(result: dict, run_date: str, top_n: int) -> str:
    series: dict[str, pd.Series] = result["series"]
    rebal = result["rebal_dates"]
    start = rebal[0].strftime("%Y-%m")
    end = series["ベンチマーク(全銘柄等ウェイト)"].index[-1].strftime("%Y-%m")

    lines = [
        f"# バックテスト結果 ({run_date})",
        "",
        f"- 期間: {start} 〜 {end} (月末リバランス、トップ{top_n}銘柄を等ウェイト保有)",
        f"- ユニバース: 日経225 + S&P500 の現構成銘柄 {result['n_universe']}銘柄",
        "- 重み設定: config.yaml の現在値を使用",
        "",
        "## ⚠️ 結果を読む際の注意",
        "",
        "- **生存者バイアス**: ユニバースが現在の構成銘柄のため、途中で上場廃止・除外された銘柄が含まれず、全戦略とベンチマークの数値は実際より上振れしやすい",
        "- **「複合」は楽観バイアスあり**: 無料データでは過去時点のPER/ROE等が取れないため、財務指標を現在値で固定している。「現在好業績と分かっている銘柄」を過去に選んだ形になり、成績は過大評価される。**バイアス無しで信頼できるのはテクニカルのみ変種**",
        "- 取引コスト・税・為替は未考慮(日米とも現地通貨建てリターンを等ウェイト合算)",
        "",
        "## 成績サマリー",
        "",
        "| 戦略 | 累積リターン | 年率リターン | 年率ボラ | シャープ | 最大下落率 |",
        "|---|---|---|---|---|---|",
    ]
    for name, s in series.items():
        s = s.dropna()
        if s.empty:
            continue
        m = _metrics(s)
        lines.append(f"| {name} | {_pct(m['total'])} | {_pct(m['cagr'])} | "
                     f"{m['vol'] * 100:.1f}% | {m['sharpe']:.2f} | {_pct(m['maxdd'])} |")

    lines += ["", "## 年別リターン", ""]
    yearly = pd.DataFrame({name: _yearly(s.dropna()) for name, s in series.items()})
    header = "| 年 | " + " | ".join(yearly.columns) + " |"
    lines += [header, "|" + "---|" * (len(yearly.columns) + 1)]
    for year, row in yearly.iterrows():
        lines.append(f"| {year} | " + " | ".join(_pct(v) for v in row) + " |")

    bench = series["ベンチマーク(全銘柄等ウェイト)"]
    lines += ["", "## 対ベンチマーク勝率 (月次)", ""]
    for name, s in series.items():
        if name.startswith(("ベンチマーク", "参考")):
            continue
        diff = (s - bench).dropna()
        win = (diff > 0).mean()
        lines.append(f"- {name}: {win * 100:.0f}% ({len(diff)}ヶ月中{int((diff > 0).sum())}勝)")

    lines += ["", "## 直近リバランスの選定銘柄", ""]
    for name, picks in result["last_picks"].items():
        lines.append(f"- {name}: {', '.join(picks)}")
    lines.append("")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> None:
    base_dir = Path(args.config).resolve().parent
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    run_date = date.today().isoformat()
    limit = args.limit or cfg["universe"]["limit"]

    uni = universe.get_universe(limit)
    tickers = uni["ticker"].tolist()

    # 現在の財務データ(メインDBのキャッシュを利用。複合変種で使う)
    main_store = Store(base_dir / cfg["data"]["db_path"])
    dataset = fetch.load_dataset(main_store, uni)
    main_store.close()

    # 長期株価はバックテスト専用DBに取得 (メインのキャッシュと分離)
    bt_store = Store(base_dir / "data" / "backtest.db")
    cache_days = 0 if args.force_refresh else HISTORY_CACHE_DAYS
    fetch.fetch_prices(bt_store, tickers, f"{args.years + 1}y", cache_days)
    _fetch_indices(bt_store, args.years)

    prices = {t: bt_store.load_prices(t) for t in tickers}
    prices = {t: p for t, p in prices.items() if not p.empty}
    idx_prices = {t: bt_store.load_prices(t) for t in INDEX_TICKERS}
    bt_store.close()
    logger.info("株価履歴を読み込み: %d銘柄", len(prices))

    result = run_backtest(prices, dataset, idx_prices, cfg["scoring"],
                          args.years, args.top)
    text = build_report(result, run_date, args.top)

    out_dir = base_dir / cfg["report"]["output_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"backtest-{run_date}.md"
    path.write_text(text, encoding="utf-8")
    logger.info("バックテストレポートを保存: %s", path)

    # 月次リターン系列もCSVで保存 (グラフ化・再分析用)
    csv_path = out_dir / f"backtest-{run_date}.csv"
    pd.DataFrame(result["series"]).to_csv(csv_path, encoding="utf-8")
    logger.info("月次リターン系列を保存: %s", csv_path)

    # Windowsのcp932コンソールで表示できない文字があっても落ちないようにする
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(text)
