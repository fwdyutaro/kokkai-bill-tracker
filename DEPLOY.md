# 公開方法（デプロイ設計）

本サイトは **依存ライブラリなしの静的サイト**（`index.html` ＋ `data_collected.js`）なので、
公開は「静的ホスティング」＋「データ更新の定期実行」の2系統に分けるのが最も簡単・安価。

```
[データ生成（重い・サーバ/CI）]            [配信（軽い・静的ホスティング）]
collect.py → crawl.py → match_refs.py      index.html + data_collected.js
   │ 日次cron                                   │ CDNで配信
   └── data_collected.js を生成・コミット ──────┘
```

## 推奨：GitHub Pages ＋ GitHub Actions（無料・全自動）

1. `bill-tracker/` をリポジトリのルートとして push。
2. Settings → Pages で「GitHub Actions」または `main` ブランチを公開元に設定。
3. `index.html` の既定データを収集版にする場合は、初期表示を `?src=collected` 相当に
   （`loadData("collected")` を既定化）。
4. `.github/workflows/update.yml`（同梱）が**毎日**パイプラインを実行し、
   更新された `data_collected.js` を自動コミット → Pages が再デプロイ。

> e5（sentence-transformers）はCIだと初回モデルDLが重い。`--no-semantic` で回すか、
> モデルをキャッシュ（actions/cache）する。LLMゲートを使う場合は `ANTHROPIC_API_KEY` を
> リポジトリ Secrets に登録し `match_refs.py --llm-gate`。

## 代替

| 方法 | 向き | 備考 |
|---|---|---|
| Cloudflare Pages / Netlify | 同上・高速CDN | ビルドコマンド不要、ディレクトリ指定で公開 |
| さくら/エックスサーバ等 + cron | 国内・既存環境 | `run_all.bat`相当をcron化しFTP/同期 |
| 自宅PC + Task Scheduler | 試験運用 | `run_all.bat` を日次実行、生成物だけを静的ホストへ同期 |

## 公開前チェックリスト（重要）

- **収集の作法**: robots.txt遵守、crawl-delay、連絡先入りUA（実装済みUAに連絡先を記載）。
  各サイトの利用規約を確認（特にRIETI等の外部配信元）。
- **著作権**: 参考文書は**本文を保存せずタイトル＋URL＋自動要約のみ**（実装済み）。
  有料・会員記事はリンクのみ。
  法案の概要は**提出理由の原文を転載せず、ローカルLLM(Ollama)で自分の言葉に要約**して掲載
  （`collect.py --llm-summary`。要約は `summaries.json` にキャッシュしCIでも再利用）。
  ※法律案・提出理由等の政府資料は著作権法13条で保護対象外との解釈もあるが、安全側に倒して要約。
- **AI生成の明示**: 概要・確信度は「AI付与・要確認」を明記（UI実装済み）。一次資料リンク併記。
- **出典・更新日**: 各リンクに発行元・tierを表示（実装済み）。フッターに出典明記（実装済み）。
- **免責**: 「審議状況・参考情報は自動生成であり正確性を保証しない。最終確認は一次資料で」を掲示。
- **リンク死活**: `linkcheck.py` を日次に組み込み、死リンクは非表示 or Wayback差し替え。
- **個人情報・負荷**: 静的配信のため利用者データ収集なし。収集対象は公的情報のみ。

## 本番でのデータ供給（発展）

静的JSONで十分だが、規模拡大時は:
- `bills.json` を簡易API（FastAPI等）化し、フロントは差分取得。
- ウォッチ登録した法案のステータス変化を**メール/RSS/Webhook通知**（差分検知は既存パイプラインで容易）。
- 全文検索（Meilisearch等）で法案横断検索。
