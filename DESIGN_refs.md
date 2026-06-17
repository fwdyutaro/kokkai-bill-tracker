# 参考リンク収集の設計

法案に付す参考情報を自動収集する仕組みの設計。
**収集（クロール＆インデックス化）** と **紐付け（マッチング＆スコアリング）** を分離するのが基本方針。

```
[ソース台帳]                [クローラ/インデクサ]            [マッチャ]              [出力]
sources.yaml  ──────▶  各ソースを巡回し documents へ  ──┐
(tier/取得方式/頻度)     (RSS/HTML/API/Selenium)        │
                                                        ▼
法案レコード(bills.json) ── 紐付けキー抽出 ──▶  構造化→キーワード→セマンティック  ──▶ bill_refs
(所管/委員会/通称/理由)                          ＋確信度スコアリング              (tier別に採用/候補)
```

既に確定的に取得できている Tier1（参議院・衆議院本文・内閣法制局・所管省庁ページ）は
法案番号で一意に紐づくため対象外。本設計が扱うのは **「どの法案に紐づくか非自明」な参考情報**。

---

## 1. ソース台帳 `sources.yaml`

巡回対象を宣言的に管理（`weekly_check` と同じ発想で取得方式を明示）。

```yaml
- id: ndl_issue_brief
  tier: 2                      # 立法補佐機関
  category: 立法補佐機関
  publisher: 国立国会図書館 調査及び立法考査局
  index_url: https://www.ndl.go.jp/jp/diet/publication/issue/index.html
  method: html                # html | rss | api | selenium
  match: title                # 既定の紐付け戦略（後述）
  freq: daily
- id: sangiin_rippou_chousa
  tier: 2
  publisher: 参議院 常任委員会調査室「立法と調査」
  index_url: https://www.sangiin.go.jp/japanese/annai/chousa/rippou_chousa/backnumber.html
  method: html
  match: committee+time       # 委員会×会期で対応
  freq: weekly
- id: shingikai_soumu
  tier: 1                     # 審議会・検討会（準一次）
  publisher: 総務省
  index_url: https://www.soumu.go.jp/main_sosiki/kenkyu/index.html
  method: html
  ministry: 総務省            # 所管フィルタのキー
  match: ministry+time+semantic
  freq: weekly
- id: rieti_report
  tier: 3
  publisher: RIETI
  index_url: https://www.rieti.go.jp/jp/rss/data/jp.rdf
  method: rss
  match: semantic
  freq: weekly
- id: nishimura_newsletter
  tier: 4
  publisher: 西村あさひ
  index_url: https://www.nishimura.com/ja/knowledge
  method: selenium           # JS描画のため
  match: semantic+keyword
  freq: weekly
```

### Tier別ソースカタログ（初期セット）

| Tier | 区分 | 主なソース | 取得方式 | 紐付け |
|---|---|---|---|---|
| 1 | 審議会・検討会 | 各省「審議会・研究会等」一覧（総務省/警察庁/法務省…） | HTML巡回 | 所管×時期×意味 |
| 1 | パブコメ | e-Gov パブリックコメント | API/HTML検索 | 案件名×所管 |
| 2 | 立法補佐機関 | 国会図書館 ISSUE BRIEF/レファレンス、参院「立法と調査」、衆院調査局 | HTML/RSS | タイトル/委員会×時期 |
| 3 | シンクタンク | RIETI, NIRA, 東京財団, 三菱総研/野村総研/日本総研/大和総研/ニッセイ基礎研 | RSS優先、無ければHTML/Selenium | セマンティック |
| 4 | 専門家解説 | 大手法律事務所のニュースレター、日弁連意見書、業界団体提言 | HTML/Selenium | セマンティック＋KW |

> 所管省庁が `enrich.py` で確定済みなので、**審議会は「その省のページだけ巡回」**でき、精度・コストとも有利。

---

## 2. データモデル（SQLite）

```sql
-- 巡回で集めた文書のインデックス（本文は保存せず、タイトル+要旨+URLのみ）
CREATE TABLE documents (
  id TEXT PRIMARY KEY,         -- url の正規化ハッシュ
  source_id TEXT,
  tier INTEGER, category TEXT, publisher TEXT,
  title TEXT, url TEXT,
  published_at TEXT,           -- 発行日（紐付けの時期判定に使用）
  abstract TEXT,               -- 自動要約 or メタディスクリプション（著作権配慮で短く）
  embedding BLOB,              -- 文ベクトル
  content_hash TEXT,           -- 変更検知
  http_status INTEGER, fetched_at TEXT
);

-- 法案と文書の紐付け
CREATE TABLE bill_refs (
  bill_id TEXT, document_id TEXT,
  score REAL, confidence INTEGER,
  matched_by TEXT,             -- ministry|title|committee|keyword|semantic（複合可）
  status TEXT,                 -- auto | candidate | confirmed | rejected
  created_at TEXT,
  PRIMARY KEY (bill_id, document_id)
);
```

文書本文は保存しない（リンク＋短い自動要約のみ）。著作権・規約配慮の中核。

---

## 3. 紐付けキー（法案レコードから抽出）

| キー | 出所 | 用途 |
|---|---|---|
| 所管省庁 | `enrich.py`（内閣法制局） | 審議会の絞り込み（強い） |
| 付託委員会 | `collect.py`（議案明細） | 「立法と調査」対応 |
| 提出日・会期 | 同上 | 時期窓（提出の −18〜+3ヶ月） |
| キーワード | 「理由」「件名」から名詞抽出（MeCab/SudachiPy） | キーワードマッチ |
| **通称** | 別途辞書 or 報道から推定 | シンクタンク/事務所の検索精度を左右 |
| 法案要約ベクトル | 「理由」を埋め込み | セマンティック検索 |

**通称辞書** `aliases.yaml` を併設（例: 閣法33 →「携帯電話不正利用防止法」「SIM不正契約対策」）。
正式名は長く一致しにくいため、通称が紐付け精度の鍵。初期は手動、将来は報道見出しから学習。

---

## 4. マッチングパイプライン（多段・再現と精度の両立）

```
1) 構造化フィルタ（高精度・候補を絞る）
   - 審議会:  document.ministry == bill.ministry  かつ  時期窓内
   - 立法補佐: title に 件名/通称/制度名 を含む（ISSUE BRIEF はほぼ法案単位）
   - 立法と調査: 付託委員会 × 会期 で対応
2) キーワード一致（中精度）
   - bill.keywords ∩ document.(title+abstract) の重なり率
3) セマンティック検索（再現重視）
   - cos(bill.embedding, document.embedding) ≥ θ
4) スコア合成 → tier別閾値で 採用/候補/棄却
```

---

## 5. 確信度スコアリング

```
score = 0.30 * tier_prior          # Tier1=1.0, 2=0.9, 3=0.6, 4=0.5
      + 0.25 * ministry_match      # 所管一致=1 / 不一致=0
      + 0.20 * semantic_sim        # 0..1（cos正規化）
      + 0.15 * keyword_overlap     # Jaccard
      + 0.10 * time_proximity      # 時期窓内で線形減衰
      - penalty(古すぎ/別会期/重複)
confidence = round(score * 100)
```

| Tier | 採用方針 | 閾値 |
|---|---|---|
| 1（一次・審議会） | 自動採用 | score ≥ 0.55 |
| 2（立法補佐） | 自動採用（タイトル一致時） | score ≥ 0.60 |
| 3-4（シンクタンク/事務所） | **候補表示**（人手 or LLM確認） | score ≥ 0.70 |

UI では Tier3-4 は確信度バー付きの「候補」として出し、Tier1-2 と区別（現行UIに実装済み）。
任意で **LLM 判定**（候補×法案要約を渡し「関連する/しない＋一文根拠」）を挟むと誤紐付けを抑制。

---

## 6. 埋め込みモデル

- 既定: **ローカル** `intfloat/multilingual-e5-large`（sentence-transformers）。日本語可・無料・オフライン。
- 代替: **Voyage AI**（`voyage-3`, Anthropic 推奨パートナー）/ OpenAI `text-embedding-3-large`。
- 法案側は「件名＋理由」、文書側は「タイトル＋abstract」をベクトル化し、`documents.embedding` に保存。

---

## 7. 重複排除・リンク死活・運用

- **正規化**: URL から utm 等を除去 → ハッシュを `documents.id`。タイトル近重複は MinHash で抑制。
- **増分巡回**: `content_hash` と Last-Modified/ETag で差分のみ更新。
- **リンク死活**: 定期 HEAD チェック。404 は Wayback（`http://archive.org/wayback/available`）の保存版URLへ。
- **巡回頻度**: 会期中は立法補佐/審議会=日次、シンクタンク/事務所=週次。

## 8. 法務・規約

- robots.txt 遵守（`urllib.robotparser`）、crawl-delay 順守、連絡先入り User-Agent。
- **本文は転載しない**（タイトル＋URL＋短い自動要約のみ）。有料・会員記事はリンクのみ。
- 各社サイトの利用規約を台帳に記録し、転載不可ソースは要約も最小化。

---

## 9. 既存コードへの組み込みと段階的実装

```
crawl.py     # sources.yaml を巡回 → documents へインデックス化（独立バッチ）
refs.py      # 法案レコード → bill_refs を生成（マッチャ＋スコアラ）
collect.py   # enrich の後段で refs.match(bill) を呼び、refs に反映
```

**実装順（小さく始める）**
1. **立法補佐機関**（国会図書館・立法と調査）… タイトル/委員会一致で高精度、ROI最大
2. **審議会**（所管省庁ページ巡回）… 所管が既知なので絞り込みが効く
3. 埋め込み基盤＋**シンクタンク**（RSS中心）
4. **法律事務所**（Selenium）＋ LLM 関連判定
5. リンク死活・Wayback・重複排除の保守機構

> まず 1 だけでも「法案ごとに ISSUE BRIEF と立法と調査が並ぶ」状態になり、体感価値が大きい。

## 10. 実装状況（2026-06 時点）

| ステップ | 状態 | 実装 |
|---|---|---|
| 1 立法補佐機関 | ✅ | 国会図書館 ISSUE BRIEF/レファレンス（年次＋RSS, 2024-26）、立法と調査。`crawl.py`/`match_refs.py` |
| 2 審議会・検討会 | ✅（9省庁） | 総務/警察/厚労/国交/金融/農水/財務/法務/環境を `ministry_index` で巡回（計~1000会議）。会議ページは h1＋WG名＋報告書名を内容語化。所管×(通称＋趣旨KW)で紐付け。文科(403)/経産(接続拒否)/内閣府(統一index無)は未対応 |
| 2.5 趣旨キーワード | ✅ | 提出理由から内容語を抽出し DF(IDF的)で希少語のみ採用。部分改正法（民法中の遺言 等）で件名が汎用でも主題語で紐付け |
| 3 意味マッチ | ✅（候補運用） | `multilingual-e5` 導入済。同義語を橋渡し可。短文は値域圧縮で精度まちまちのため top-1＋マージンで「要確認候補」に限定。本格精度化は doc側に要旨を持たせる＋LLM関連判定 |
| 4 法律事務所 | 未 | Selenium＋LLM関連判定（今後） |
| 5 保守機構 | 未 | リンク死活・Wayback・重複排除の一部のみ（pid正規化は実装済み） |

**ステップ3の知見**:
- 文字n-gram TF-IDFは語彙一致の再発見に留まり、真の同義（太陽光パネル↔太陽電池 等）は橋渡し不可。
- `multilingual-e5` は同義を橋渡しできる（実測: 太陽光パネル↔太陽電池=0.86 等）が、
  **短いタイトル同士では値域が0.75〜0.89に圧縮**され、無関係でも0.75前後に達するため
  絶対閾値での自動採用は誤マッチが多い。→ 法案ごとtop-1＋平均からのマージンで「要確認候補」に限定。
- 本格的な精度化には **(a) doc側にも要旨(abstract)を持たせて長文どうしで比較**、
  **(b) 候補に対する LLM 関連判定（related?＋一文根拠）** が有効。これが次の打ち手。
