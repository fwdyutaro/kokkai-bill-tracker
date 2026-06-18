# -*- coding: utf-8 -*-
"""
参考リンク収集 — マッチャ／スコアラ（ステップ1: タイトル一致）
refs.db の文書を法案(bills.json)に紐付け、Tier2参考リンクとして付与する。

  python match_refs.py                 # bills.json を読み、紐付けて上書き＋data_collected.js出力
  python match_refs.py --in full.json  # 別の法案リストに対して実行
  python match_refs.py --dry           # 書き込まずレポートのみ

紐付けロジック（ステップ1は語彙ベース。意味ベースは後続ステップで追加）:
  強  : 法案名(boilerplate除去)が文書タイトルに部分一致      → conf 0.92
  中  : 法案名の特徴的なセグメント(6字以上)が部分一致        → conf 0.6+
  弱  : 文字3-gram Jaccard ≥ 0.22                          → conf 0.4+
Tier2は score ≥ 0.55 で自動採用（DESIGN_refs.md のスコア方針に準拠）。
"""
import argparse, json, os, re, sqlite3
from datetime import date, timedelta
try:
    import yaml
except ImportError:
    yaml = None

# 発行元ごとの採用時期ウィンドウ（法案提出日基準, 前/後の日数）。
# 立法と調査は提出前後3か月程度に限定。NDLの調査資料は論点化が先行するため広め。
TIME_WINDOW = {
    "立法と調査": (100, 130),
    "国立国会図書館": (600, 150),
}


def _pdate(s):
    """'YYYY-MM-DD' または 'YYYY'（年のみは年央扱い）を date に。失敗時 None。"""
    if not s:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"(\d{4})$", s)
    return date(int(m[1]), 7, 1) if m else None


def time_ok(doc, submitted_on):
    """文書の発行時期が法案提出日の許容ウィンドウ内か。日付不明は除外しない。"""
    s = _pdate(submitted_on)
    dt = _pdate(doc.get("published_at"))
    if not s or not dt:
        return True
    pub = doc.get("publisher") or ""
    for kw, (before, after) in TIME_WINDOW.items():
        if kw in pub:
            return s - timedelta(days=before) <= dt <= s + timedelta(days=after)
    return True

ATTACH_THRESHOLD = 0.55
SEM_MARGIN = 0.06   # 意味類似: 法案平均からの上振れ要件（際立った近さのみ採用）
BOILER = re.compile(
    r"(の一部を改正する等の法律案|の一部を改正する法律案|に関する法律の一部を改正する法律案"
    r"|に関する法律案|の整備に関する法律案|に関する法律|法律案|案)$")


def norm(s):
    return re.sub(r"[\s　]+", "", s or "")


def bill_terms(title):
    core = BOILER.sub("", norm(title))
    terms = {core}
    # 「等」「及び」での分割で生じる断片のうち、法令名らしいものだけ採用。
    # 「に関する法律」等の汎用断片（接続辞で始まる）は除外して誤マッチを防ぐ。
    for seg in re.split(r"等|及び|並びに", core):
        if len(seg) >= 6 and not seg.startswith(("に関する", "関する", "する", "及び")):
            terms.add(seg)
    return core, {t for t in terms if len(t) >= 6}


# 法令名に頻出する定型句。3-gram類似の前に除去し、内容語のみで比較する
# （除去しないと「の一部を改正する法律案」等の共通部分で誤マッチする）。
NOISE = re.compile(
    r"の一部を改正する等の法律|の一部を改正する法律|の一部を改正する|の施行に伴う関係法律の整備"
    r"|に伴う関係法律の整備|に関する法律|に関する|についての|について|に係る|に関する法律案"
    r"|等の|法律案|法律|措置|に関し|する")


def denoise(s):
    return NOISE.sub("", norm(s))


def trigrams(s):
    s = norm(s)
    return {s[i:i + 3] for i in range(len(s) - 2)} if len(s) >= 3 else {s}


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def score(bill_title, doc_title):
    core, terms = bill_terms(bill_title)
    dt = norm(doc_title)
    if core and len(core) >= 6 and core in dt:
        return 0.92, "法案名が一致"
    hit = max((t for t in terms if t in dt), key=len, default=None)
    if hit:
        return min(0.9, 0.6 + len(hit) / 40), f"特徴語が一致（{hit}）"
    # 定型句を除去した内容語どうしで3-gram類似（誤マッチ抑制）
    j = jaccard(trigrams(denoise(core)), trigrams(denoise(doc_title)))
    if j >= 0.30:
        return min(0.85, 0.45 + j), f"語彙類似（3-gram {j:.2f}）"
    return 0.0, ""


def build_semantic(bills, docs):
    """法案×文書 のコサイン類似度行列を返す（プラガブルな意味層）。

    返り値: (sims[bill_idx][doc_idx], threshold, backend名) または None。

    backend は優先順に自動選択:
      1) sentence-transformers の multilingual-e5  … 真の文意ベース（最良）
      2) Voyage AI (voyage-3)                      … 要 VOYAGE_API_KEY
      3) TF-IDF 文字n-gram                          … フォールバック（※下記の限界）

    ※TF-IDFは「文字の重なり」しか見ないため、真の同義
      （太陽光パネル↔太陽電池 / 本人確認↔ガバナンス / ドローン↔小型無人機）は
      橋渡しできない。高閾値では語彙一致の再発見に留まり、低閾値では
      表層一致（防災↔防衛 等）の誤マッチが出る。実運用の意味マッチは
      1) か 2) を入れること。当面の同義吸収は aliases.yaml が担う。
    """
    # 法案側は趣旨(提出理由)を使うと表現が豊かになり精度が上がる（件名は短く汎用的）
    bill_texts = [norm((b.get("summary") or "") + " " + b["title"])[:400] for b in bills]
    doc_texts = [norm(d["title"]) for d in docs]

    # 1) multilingual-e5（インストールされていれば最良）
    try:
        from sentence_transformers import SentenceTransformer
        m = SentenceTransformer("intfloat/multilingual-e5-small")
        be = m.encode(["query: " + t for t in bill_texts], normalize_embeddings=True)
        de = m.encode(["passage: " + t for t in doc_texts], normalize_embeddings=True)
        return be @ de.T, 0.85, "multilingual-e5"
    except Exception:
        pass

    # 3) TF-IDF 文字n-gram（フォールバック＝語彙近接の代理。閾値は保守的に）
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        return None
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1)
    M = vec.fit_transform(doc_texts + bill_texts)
    return cosine_similarity(M[len(docs):], M[:len(docs)]), 0.30, "tfidf(char)"


def load_docs(db="refs.db"):
    con = sqlite3.connect(db)
    return [dict(zip(["tier", "category", "publisher", "title", "url", "published_at", "ministry"], r))
            for r in con.execute(
        "SELECT tier,category,publisher,title,url,published_at,ministry FROM documents")]


def load_aliases():
    if yaml and os.path.exists("aliases.yaml"):
        return yaml.safe_load(open("aliases.yaml", encoding="utf-8")).get("aliases", [])
    return []


def bill_keywords(bill, aliases):
    """件名に該当するエイリアスの関連キーワード集合を返す。"""
    kw = set()
    for a in aliases:
        if a["match"] in bill["title"]:
            kw.update(a["keywords"])
    return kw


# 趣旨(提出理由)から内容語を抽出する際に落とす汎用語
PURPOSE_STOP = set(
    "法律案 法律 法令 改正 規定 措置 必要 整備 推進 関係 制度 事業 場合 一部 施行 理由 規制 対策 "
    "状況 確保 向上 実施 適正 防止 促進 円滑 観点 内容 見直 拡大 強化 導入 創設 充実 増進 提出 "
    "当該 一環 我国 近年 最近 鑑 法律案提出 提出理由 システム 運営 管理 利用 業務 機関 措置等 "
    "ルール サービス ネットワーク データ ケース".split())
# カタカナ語(3字以上) または 漢字連続(2〜8字)
KW_RE = re.compile(r"[ァ-ヴー]{3,}|[一-龠々〆ヶ]{2,8}")


def keywords_from_purpose(summary):
    """提出理由・趣旨から内容語キーワードを抽出（形態素解析器なしの近似）。
    件名が汎用（民法等の一部を改正…）でも、趣旨には主題語（遺言, 本人確認 等）が出るため、
    そこから紐付けキーワードを作る。"""
    if not summary:
        return set()
    out = set()
    for c in KW_RE.findall(norm(summary)):
        if re.fullmatch(r"[ァ-ヴー]{3,}", c):     # カタカナ語(3字以上)
            if c not in PURPOSE_STOP:
                out.add(c)
            continue
        c = re.sub(r"[等及並的]+$", "", c)         # 末尾の接続辞・形容辞を除去（進展等/総合的→…）
        # 漢字は4字以上のみ採用（3字以下は語境界をまたぐ断片や汎用が多い）
        if len(c) >= 4 and c not in PURPOSE_STOP:
            out.add(c)
    return out


def score_shingikai(bill, doc, keywords):
    """審議会・検討会: 所管一致を前提に、通称キーワード or 語彙類似で判定。"""
    hit = next((k for k in keywords if k in doc["title"]), None)
    if hit:
        return 0.82, f"所管一致＋関連語（{hit}）"
    j = jaccard(trigrams(denoise(BOILER.sub('', norm(bill['title'])))), trigrams(denoise(doc["title"])))
    if j >= 0.18:
        return 0.55 + j, f"所管一致＋語彙類似（{j:.2f}）"
    return 0.0, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", default="bills.json")
    ap.add_argument("--db", default="refs.db")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--no-semantic", action="store_true", help="意味類似を無効化")
    ap.add_argument("--llm-gate", action="store_true",
                    help="弱い候補をLLMで関連性検証（要 ANTHROPIC_API_KEY）")
    args = ap.parse_args()
    llm = None
    if args.llm_gate:
        import llm_gate as llm

    bills = json.load(open(args.infile, encoding="utf-8"))
    docs = load_docs(args.db)
    aliases = load_aliases()

    # 趣旨キーワードのコーパス文書頻度(DF)を事前計算。
    # DFが小さい＝希少＝識別力が高い語だけを採用する（IDF的フィルタ）。
    doc_norm = [norm(d["title"]) for d in docs]
    allkw = set().union(*[keywords_from_purpose(b.get("summary")) for b in bills]) or set()
    df = {k: sum(1 for dn in doc_norm if k in dn) for k in allkw}
    MAX_DF = 4   # この件数を超えて出現する語は汎用とみなし不採用

    sem = None if args.no_semantic else build_semantic(bills, docs)
    sims, sem_th, sem_backend = sem if sem else (None, 1.0, None)
    print(f"法案 {len(bills)} 件 × 文書 {len(docs)} 件で照合"
          f"{f'（意味類似: {sem_backend}, 閾値{sem_th}）' if sims is not None else ''} ...\n")

    total_attached = 0
    for bi, b in enumerate(bills):
        existing = {r["url"] for r in b["refs"]}
        # 趣旨由来キーワードのうち、コーパスで希少(DF<=MAX_DF)な識別力の高い語だけ採用
        pkw = {k for k in keywords_from_purpose(b.get("summary")) if 1 <= df.get(k, 0) <= MAX_DF}
        kw = bill_keywords(b, aliases) | pkw                     # 審議会用（通称＋趣旨）
        cands = []
        for di, d in enumerate(docs):
            if d["category"] == "審議会・検討会":
                # 所管省庁が一致する会議のみ対象（precision確保）
                if not d.get("ministry") or d["ministry"] not in (b.get("ministry") or ""):
                    continue
                sc, why = score_shingikai(b, d, kw)
            else:
                sc, why = score(b["title"], d["title"])
                # 件名で一致しなければ、趣旨キーワードで照合（部分改正法に有効）
                if sc < ATTACH_THRESHOLD:
                    hit = next((k for k in sorted(pkw, key=len, reverse=True)
                                if k in norm(d["title"])), None)
                    if hit:
                        sc, why = min(0.8, 0.58 + len(hit) / 30), f"趣旨キーワード一致（{hit}）"
            if sc >= ATTACH_THRESHOLD and d["url"] not in existing \
                    and time_ok(d, b.get("submittedOn")):
                cands.append((sc, why, d))

        # 意味類似（埋め込み）: 法案ごとに「際立って近い」上位2件だけを候補として採用。
        # 値域が圧縮されるため絶対閾値に加え、その法案の平均からのマージンを要求する。
        if sims is not None:
            row = sims[bi]
            mean = float(sum(row) / len(row))
            matched_urls = existing | {d["url"] for _, _, d in cands}
            picked = 0
            for di in sorted(range(len(docs)), key=lambda j: -row[j]):
                cos = float(row[di])
                if cos < sem_th:
                    break
                if cos - mean < SEM_MARGIN:
                    continue
                d = docs[di]
                if d["url"] in matched_urls:
                    continue
                if d["category"] == "審議会・検討会" and \
                   (not d.get("ministry") or d["ministry"] not in (b.get("ministry") or "")):
                    continue
                if not time_ok(d, b.get("submittedOn")):
                    continue
                cands.append((min(0.7, 0.4 + cos / 3),
                              f"意味類似・要確認候補（{sem_backend} {cos:.2f}）", d))
                picked += 1
                if picked >= 1:          # 法案ごとtop-1のみ（短文e5は精度がまちまちのため）
                    break
        if llm:
            cands = llm.gate(b, cands, verbose=args.dry)
        cands.sort(key=lambda x: -x[0])
        for sc, why, d in cands[:5]:               # 1法案あたり上位5件まで
            b["refs"].append({
                "tier": d["tier"], "cat": d["category"], "pub": d["publisher"],
                "title": re.sub(r"\s+", " ", d["title"]).strip(), "url": d["url"],
                "conf": round(sc * 100), "confNote": f"自動紐付け: {why}",
            })
            total_attached += 1
        if cands:
            print(f"● {b['no']} {b['title'][:34]}")
            for sc, why, d in cands[:5]:
                print(f"    {round(sc*100):3d}%  [{d['publisher'][:12]}] {d['title'][:46]}  〈{why}〉")

    print(f"\n紐付け {total_attached} 件")
    if not args.dry:
        json.dump(bills, open(args.infile, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        with open("data_collected.js", "w", encoding="utf-8") as f:
            f.write("window.BILLS = ")
            json.dump(bills, f, ensure_ascii=False, indent=2)
            f.write(";\n")
        print(f"出力: {args.infile} / data_collected.js")


if __name__ == "__main__":
    main()
