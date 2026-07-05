"""Markdownレポート生成"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def _fmt(v, pct: bool = False, digits: int = 1) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "-"
    return f"{v * 100:.{digits}f}%" if pct else f"{v:.{digits}f}"


def _table(df: pd.DataFrame, new_tickers: set[str]) -> str:
    lines = [
        "| 順位 | ティッカー | 銘柄名 | 市場 | スコア | PER | ROE | 配当利回り | 3ヶ月騰落 | RSI | |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for _, r in df.iterrows():
        mark = "🆕" if r["ticker"] in new_tickers else ""
        name = str(r["name"])[:20] if pd.notna(r["name"]) else "-"
        dy = r.get("dividendYield")
        dy_str = f"{dy:.2f}%" if dy is not None and pd.notna(dy) else "-"
        lines.append(
            f"| {r['rank']} | {r['ticker']} | {name} | {r['market']} "
            f"| {_fmt(r['score'])} | {_fmt(r['trailingPE'])} "
            f"| {_fmt(r['returnOnEquity'], pct=True)} | {dy_str} "
            f"| {_fmt(r['ret_63d'], pct=True)} "
            f"| {_fmt(r['rsi'], digits=0)} | {mark} |"
        )
    return "\n".join(lines)


def _breakdown_table(df: pd.DataFrame) -> str:
    lines = [
        "| ティッカー | 割安 | 収益性 | 成長 | トレンド | 勢い | 出来高 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in df.iterrows():
        cells = " | ".join(_fmt(r[f"z_{c}"], digits=2)
                           for c in ["value", "quality", "growth", "trend", "momentum", "volume"])
        lines.append(f"| {r['ticker']} | {cells} |")
    return "\n".join(lines)


def build_report(ranked: pd.DataFrame, run_date: str, cfg: dict,
                 new_tickers: set[str]) -> str:
    top_n = cfg["top_n"]
    per_market = cfg["top_n_per_market"]
    top = ranked.head(top_n)

    parts = [
        f"# 銘柄スクリーニング結果 {run_date}",
        "",
        f"対象: {len(ranked)}銘柄 (JP {len(ranked[ranked['market'] == 'JP'])} / "
        f"US {len(ranked[ranked['market'] == 'US'])})",
        "",
        f"## 総合トップ{top_n}",
        "",
        _table(top, new_tickers),
        "",
        "## スコア内訳 (トップ銘柄 / 市場内偏差)",
        "",
        _breakdown_table(top),
    ]
    for market, label in [("JP", "日本株"), ("US", "米国株")]:
        sub = ranked[ranked["market"] == market].head(per_market)
        if not sub.empty:
            parts += ["", f"## {label}トップ{per_market}", "", _table(sub, new_tickers)]

    parts += [
        "",
        "---",
        "※ 本レポートは機械的なスクリーニング結果であり、投資判断はご自身の責任で行ってください。",
    ]
    return "\n".join(parts)


def save_report(text: str, run_date: str, output_dir: str | Path) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{run_date}.md"
    path.write_text(text, encoding="utf-8")
    return path
