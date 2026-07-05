# stock-screener

日本株(日経225)と米国株(S&P500)を対象に、ファンダメンタル指標とテクニカル指標の複合スコアで銘柄をランキングするスクリーナーです。

> ⚠️ 本ツールは機械的なスクリーニング結果を出力するものであり、投資助言ではありません。投資判断はご自身の責任で行ってください。

## 仕組み

```
銘柄リスト取得 → yfinanceでデータ取得(SQLiteキャッシュ) → 指標計算
→ 市場内で偏差値化して重み付き合算 → ランキング → Markdownレポート + Discord通知
```

- **ファンダメンタル**: PER・PBR(割安)/ ROE・営業利益率(収益性)/ 売上・EPS成長率(成長)
- **テクニカル**: 50日・200日移動平均乖離(トレンド)/ 3ヶ月リターン・MACD(勢い)/ 出来高トレンド
- RSIが75超の過熱銘柄にはペナルティ
- 各指標は市場(JP/US)ごとに正規化するため、日米の分布差に影響されません
- 重み付けは [config.yaml](config.yaml) で調整可能

## 使い方 (ローカル)

```powershell
# 初回セットアップ
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 実行 (全銘柄: 初回は財務データ取得に20〜30分程度かかります)
.venv\Scripts\python -m screener run

# テスト実行 (各市場8銘柄のみ・通知なし)
.venv\Scripts\python -m screener run --limit 8 --no-notify

# キャッシュを無視して再取得
.venv\Scripts\python -m screener run --force-refresh
```

レポートは `reports/YYYY-MM-DD.md` に保存されます。

## Discord通知の設定

1. Discordのサーバー設定 → 連携サービス → ウェブフック → 新しいウェブフック からURLをコピー
2. 環境変数に設定: `$env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."`

未設定の場合、通知は自動的にスキップされます。

## 定期実行 (GitHub Actions)

このリポジトリをGitHubにpushすると、[.github/workflows/screen.yml](.github/workflows/screen.yml) により**毎週土曜 07:00 JST** に自動実行されます。

1. GitHubにリポジトリを作成してpush
2. リポジトリの Settings → Secrets and variables → Actions で `DISCORD_WEBHOOK_URL` を登録
3. Actionsタブから手動実行(workflow_dispatch)も可能

レポートは Actions の成果物としてダウンロードでき、`reports/` にも自動コミットされます。

## 構成

| ファイル | 役割 |
|---|---|
| `screener/universe.py` | 日経225・S&P500の銘柄リスト取得 |
| `screener/fetch.py` | yfinanceでの株価・財務データ取得 |
| `screener/store.py` | SQLiteキャッシュ |
| `screener/indicators.py` | テクニカル指標計算 |
| `screener/scoring.py` | 偏差値化と複合スコアリング |
| `screener/report.py` | Markdownレポート生成 |
| `screener/notify.py` | Discord Webhook通知 |
| `config.yaml` | 重み・対象・出力の設定 |

## 既知の制限

- yfinanceは非公式APIのため、レート制限や仕様変更で取得に失敗することがあります(キャッシュと再実行で概ね回復します)
- 財務データは銘柄によって欠損があり、欠損指標は中立(0)として扱われます
- スコアの重み付けはバックテストで検証されたものではありません
