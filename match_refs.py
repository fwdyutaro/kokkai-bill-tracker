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
import argparse, json, os, re, sqlite3, sys, unicodedata
from datetime import date, timedelta
from apply_suppressions import _bill_key
from data_output import render_data_js
import report_laws
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
    # 文書側も要旨(PDF冒頭)があれば連結して長文比較に（短文同士の値域圧縮を緩和）
    doc_texts = [norm(d["title"] + " " + (d.get("abstract") or ""))[:500] for d in docs]

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
    cols = ["tier", "category", "publisher", "title", "url", "published_at", "ministry",
            "abstract", "parent_url"]
    have = {r[1] for r in con.execute("PRAGMA table_info(documents)")}
    # 旧DB（abstract / parent_url 列が無い）でも読めるよう、無い列は NULL で補う
    select = ",".join(c if c in have else f"NULL AS {c}" for c in cols)
    rows = con.execute(f"SELECT {select} FROM documents")
    return [dict(zip(cols, r)) for r in rows]


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


# ---- 審議会の開催時期検証（候補になった会議ページのみ取得・キャッシュ） ------
MEETING_DATE_CACHE = "meeting_dates.json"
_DATE_PAT = re.compile(r"(20\d\d)\s*[年./-]\s*(\d{1,2})")
# 和暦（令和）。省庁の会議ページは「令和８年８月10日」のように全角数字が混じる
_REIWA_PAT = re.compile(r"令和\s*(\d{1,2}|元)\s*年\s*(\d{1,2})\s*月")


def page_months(html):
    """ページ内に現れる年月を 'YYYY-MM' の集合で返す（西暦・令和の両方）。"""
    html = unicodedata.normalize("NFKC", html or "")
    months = {f"{y}-{int(m):02d}" for y, m in _DATE_PAT.findall(html)
              if 1 <= int(m) <= 12 and 2000 <= int(y) <= 2099}
    for y, m in _REIWA_PAT.findall(html):
        year = 2018 + (1 if y == "元" else int(y))
        if 1 <= int(m) <= 12:
            months.add(f"{year}-{int(m):02d}")
    return months


def load_meeting_dates():
    if os.path.exists(MEETING_DATE_CACHE):
        try:
            return json.load(open(MEETING_DATE_CACHE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def latest_meeting_month(url, cache):
    """会議ページ内の最新の年月(YYYY-MM)を返す。取得不可は '' をキャッシュ。"""
    if url in cache:
        return cache[url]
    try:
        from crawl import get_html
        months = page_months(get_html(url))
        val = max(months) if months else ""
    except Exception:
        val = ""
    cache[url] = val
    return val


def shingikai_time_ok(doc, submitted_on, cache):
    """審議会候補の開催時期チェック。最新活動が提出の3年前〜6か月後の窓に収まるか。
    ページ未取得・日付不明・報告書PDFは除外しない（安全側）。"""
    if doc["category"] != "審議会・検討会":
        return True
    url = doc["url"].split("#")[0]
    if url.lower().endswith(".pdf"):
        return True
    s = _pdate(submitted_on)
    if not s:
        return True
    ym = latest_meeting_month(url, cache)
    if not ym:
        return True
    last = date(int(ym[:4]), int(ym[5:7]), 28)
    return s - timedelta(days=1095) <= last <= s + timedelta(days=180)


def score_shingikai(bill, doc, keywords):
    """審議会・検討会: 所管一致を前提に、通称キーワード or 語彙類似で判定。"""
    hit = next((k for k in keywords if k in doc["title"]), None)
    if hit:
        return 0.82, f"所管一致＋関連語（{hit}）"
    j = jaccard(trigrams(denoise(BOILER.sub('', norm(bill['title'])))), trigrams(denoise(doc["title"])))
    if j >= 0.18:
        return 0.55 + j, f"所管一致＋語彙類似（{j:.2f}）"
    return 0.0, ""


# ---- 取り下げ（承認済み suppressions.json）の判定 ------------------------------
def load_suppression_check(path="suppressions.json"):
    """承認済み取り下げに該当するかを返す関数 check(bill, url) を作る。

    apply_suppressions.py と同じく bill_id 付きの依頼は ID で照合し、bill_id の
    無い旧形式は議案番号で照合する。候補生成段階では過剰に除外しても実害が無いため、
    旧形式は同番号の全レコードに効かせ、従来どおり bill_no の完全一致も除外する。
    """
    entries = []
    if os.path.exists(path):
        try:
            entries = [x for x in json.load(open(path, encoding="utf-8"))
                       if x.get("status") == "approved" and x.get("url")]
        except Exception:
            entries = []
    by_id = {(x["bill_id"], x["url"]) for x in entries if x.get("bill_id")}
    by_no = {(x.get("bill_no"), x["url"]) for x in entries if x.get("bill_no")}
    legacy = {(_bill_key(x.get("bill_no")), x["url"]) for x in entries
              if not x.get("bill_id") and _bill_key(x.get("bill_no"))}

    def check(bill, url):
        return ((bill.get("id"), url) in by_id
                or (bill.get("no"), url) in by_no
                or (_bill_key(bill.get("no")), url) in legacy)
    return check


# ---- 意味類似候補のキャッシュ（週次ローカル処理 → 日次CIへの引き継ぎ） -----------
# CI は e5 を持たず、collect.py が最新会期のレコードを毎晩作り直すため、週次処理が
# 付けた意味類似リンクは翌晩に消える。週次処理（権威ある意味類似バックエンド使用時）が
# 「各法案に付いている意味類似リンク」を semantic_cache.json に保存し、意味類似を
# 計算できない実行ではそれを再適用する。再適用時も取り下げとLLMゲートを必ず通す。
SEMANTIC_CACHE = "semantic_cache.json"
SEMANTIC_CACHE_VERSION = 1
SEMANTIC_MARK = "意味類似"
AUTHORITATIVE_SEM_BACKENDS = ("multilingual-e5",)
_NOTE_PREFIX = "自動紐付け: "


def bill_fingerprint(bill):
    """IDの付け替え・再利用で別法案に誤適用しないための照合キー。"""
    return {"no": norm(bill.get("no")), "title": norm(bill.get("title"))}


def fingerprint_matches(entry, bill):
    return {k: entry.get(k) for k in ("no", "title")} == bill_fingerprint(bill)


def base_why(conf_note):
    """confNote から LLM注記を除いた紐付け根拠（ゲート再通過時に注記を付け直す）。"""
    why = conf_note or ""
    if why.startswith(_NOTE_PREFIX):
        why = why[len(_NOTE_PREFIX):]
    return why.split("／LLM確認", 1)[0]


def load_semantic_cache(path=SEMANTIC_CACHE):
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict) or data.get("version") != SEMANTIC_CACHE_VERSION:
        return {}
    bills = data.get("bills")
    return bills if isinstance(bills, dict) else {}


def build_semantic_cache(bills, suppressed=None, gate=None):
    """各法案に現在付いている意味類似リンクを {bill_id: entry} にまとめる。

    取り下げ済み・LLMゲートで「無関係」と判定済みのものは保存しない。
    """
    out = {}
    for b in bills:
        bid = b.get("id")
        if not bid:
            continue
        cands = []
        for r in b.get("refs", []):
            note, url = r.get("confNote") or "", r.get("url")
            if SEMANTIC_MARK not in note or not url:
                continue
            if suppressed and suppressed(b, url):
                continue
            if gate is not None:
                v = gate.cached_verdict(b, {"url": url, "title": r.get("title") or ""})
                if v is not None and not v.get("related"):
                    continue
            cands.append({
                "url": url, "score": round((r.get("conf") or 0) / 100, 2),
                "why": base_why(note), "tier": r.get("tier"), "cat": r.get("cat"),
                "pub": r.get("pub"), "title": r.get("title"),
            })
        if cands:
            out[bid] = {**bill_fingerprint(b),
                        "candidates": sorted(cands, key=lambda c: c["url"])}
    return dict(sorted(out.items()))


def save_semantic_cache(entries, path=SEMANTIC_CACHE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": SEMANTIC_CACHE_VERSION, "bills": entries}, f,
                  ensure_ascii=False, indent=1)
        f.write("\n")


def replay_candidates(bill, entry, taken, suppressed=None):
    """キャッシュ済みの意味類似候補を (score, why, doc) で返す（ゲート前）。

    IDが一致しても議案番号・件名が変わっていれば別法案とみなし適用しない。
    既に付いているURL・取り下げ済みURLは除く。
    """
    if not entry or not fingerprint_matches(entry, bill):
        return []
    out = []
    for c in entry.get("candidates") or []:
        url = c.get("url")
        if not url or url in taken or SEMANTIC_MARK not in (c.get("why") or ""):
            continue
        if suppressed and suppressed(bill, url):
            continue
        doc = {"tier": c.get("tier"), "category": c.get("cat"), "publisher": c.get("pub") or "",
               "title": c.get("title") or "", "url": url}
        out.append((float(c.get("score") or 0), c["why"], doc))
        taken = taken | {url}
    return out


# ---- 報告書・提言が改正対象の法令に言及しているか ---------------------------
# 件名・趣旨の語が会議名と重ならない改正法案でも、前段の審議会・検討会の報告書は
# 「〇〇法の中に規定することが考えられる」のように改正先の法令名を書くことが多い
# （report_laws.py 参照）。所管一致・開催時期・LLMゲートは他の審議会候補と同じ。

# この数を超える会議の報告書に出てくる法令名は、その省庁では汎用とみなして使わない
MAX_LAW_COUNCILS = 3
# 骨子・案・中間取りまとめより最終版を優先して添付する
_DRAFT_RE = re.compile(r"骨子|素案|たたき台|中間|（案）|\(案\)|案）")


def build_report_index(docs, laws_cache):
    """{会議ページURL: {"doc": 会議ページ, "reports": [(報告書doc, 言及)]}} を作る。"""
    by_url = {d["url"]: d for d in docs}
    index = {}
    for d in docs:
        parent = d.get("parent_url")
        if d["category"] != "審議会・検討会" or not parent or parent not in by_url:
            continue
        entry = laws_cache.get(d["url"])
        # 改正・規定の文脈での言及（amend）だけを使う。単に法令名に触れた報告書は拾わない
        if not entry or not entry.get("amend"):
            continue
        c = index.setdefault(parent, {"doc": by_url[parent], "reports": []})
        c["reports"].append((d, entry["amend"]))
    return index


def law_mention_candidates(bill, index, mcache, max_councils=MAX_LAW_COUNCILS):
    """件名の改正対象法令に言及する同所管の会議ページと、その代表的な報告書を候補にする。"""
    ministry = bill.get("ministry") or ""
    out = []
    for law in report_laws.amended_laws(bill["title"]):
        councils = []
        for c in index.values():
            cm = c["doc"].get("ministry")
            if not cm or cm not in ministry:
                continue
            reps = [(report_laws.mentions_law(m, law), r) for r, m in c["reports"]]
            reps = [(n, r) for n, r in reps if n > 0]
            if reps:
                councils.append((c, reps))
        if not councils or len(councils) > max_councils:
            continue
        for c, reps in councils:
            if not shingikai_time_ok(c["doc"], bill.get("submittedOn"), mcache):
                continue
            total = sum(n for n, _ in reps)
            _, best = max(reps, key=lambda x: (not _DRAFT_RE.search(x[1]["title"]), x[0]))
            note = f"この会議の報告書・提言は「{law}」の改正・規定に{total}回言及している"
            why = f"所管一致＋報告書が改正対象の法令に言及（{law}）"
            out.append((0.84, why, dict(c["doc"], note=note)))
            out.append((0.83, why, dict(best, note=note)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", default="bills.json")
    ap.add_argument("--db", default="refs.db")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--no-semantic", action="store_true",
                    help="意味類似の計算を無効化（週次処理の結果は semantic_cache.json から再適用）")
    ap.add_argument("--no-semantic-cache", action="store_true",
                    help="semantic_cache.json の再適用・更新をしない")
    ap.add_argument("--llm-gate", action="store_true",
                    help="(互換用・現在は自動判定) LLMゲートを強制有効化")
    ap.add_argument("--no-llm-gate", action="store_true", help="LLMゲートを無効化")
    args = ap.parse_args()
    # LLMゲート: Ollama/APIキーが使えれば判定し、無くてもキャッシュだけで適用する
    gate = None
    if not args.no_llm_gate:
        from llm_gate import Gate
        g = Gate()
        if g.available():
            gate = g
            mode = g.backend or f"キャッシュのみ・{len(g.cache)}件"
            print(f"LLMゲート有効（{mode}）", file=sys.stderr)

    bills = json.load(open(args.infile, encoding="utf-8"))
    if gate:
        gate.set_bills(bills)   # 旧形式キャッシュの引き継ぎ可否（議案番号の重複）を判定
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
    # 権威ある意味類似（e5）を計算できた実行はキャッシュを更新し、
    # できなかった実行（CI の --no-semantic 等）はキャッシュを再適用する。
    sem_authoritative = sims is not None and sem_backend in AUTHORITATIVE_SEM_BACKENDS
    sem_cache = {} if (args.no_semantic_cache or sem_authoritative) else load_semantic_cache()
    print(f"法案 {len(bills)} 件 × 文書 {len(docs)} 件で照合"
          f"{f'（意味類似: {sem_backend}, 閾値{sem_th}）' if sims is not None else ''}"
          f"{f'（意味類似キャッシュ: {len(sem_cache)}法案分を再適用）' if sem_cache else ''} ...\n")

    # 却下済み(bill,url)ペアは候補から除外（取り下げ依頼の還流。top-5枠も空けられる）
    suppressed = load_suppression_check()
    mcache = load_meeting_dates()
    replay_stats = {"cand": 0, "kept": 0, "mismatch": 0}
    report_index = build_report_index(docs, report_laws.load_cache())

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
                    and not suppressed(b, d["url"]) \
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
                if suppressed(b, d["url"]):
                    continue
                cands.append((min(0.7, 0.4 + cos / 3),
                              f"意味類似・要確認候補（{sem_backend} {cos:.2f}）", d))
                picked += 1
                # ゲート有効時は候補生成を緩めてLLM検証に委ねる（再現↑・精度はゲートが担保）
                if picked >= (3 if gate else 1):
                    break
        # 審議会候補の開催時期チェック（候補になった会議ページのみ取得）
        cands = [(sc, why, d) for sc, why, d in cands
                 if shingikai_time_ok(d, b.get("submittedOn"), mcache)]
        # 報告書が改正対象の法令に言及する会議（開催時期は関数内で確認済み）
        have = {d["url"] for _, _, d in cands}
        # 同じ会議ページ（ページ内アンカー違いを含む）が既に付いていれば重ねて付けない
        pages = {u.split("#")[0] for u in existing | have}
        for sc, why, d in law_mention_candidates(b, report_index, mcache):
            if (d["url"] in existing or d["url"] in have or d["url"].split("#")[0] in pages
                    or suppressed(b, d["url"])):
                continue
            have.add(d["url"])
            pages.add(d["url"].split("#")[0])
            cands.append((sc, why, d))
        # 週次処理で得た意味類似候補の再適用（時期・所管の検証は計算時に済んでいる）
        replayed = []
        entry = sem_cache.get(b.get("id"))
        if entry and not fingerprint_matches(entry, b):
            replay_stats["mismatch"] += 1
        elif entry:
            replayed = replay_candidates(b, entry, existing | have, suppressed)
        if gate:
            cands = gate.filter(b, cands, verbose=args.dry)
        if replayed:
            replay_stats["cand"] += len(replayed)
            # 「関連する」と判定済みのものだけ再適用する。ゲートが無い・未判定なら
            # 検証できないので出さない（却下済みリンクの復活を防ぐ）。
            replayed = gate.filter(b, replayed, verbose=args.dry, require_verdict=True) \
                if gate else []
            replay_stats["kept"] += len(replayed)
            cands += replayed
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

    json.dump(mcache, open(MEETING_DATE_CACHE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    if gate:
        gate.save()
        st = gate.stats
        print(f"\nLLMゲート: 採用{st['pass']} 除外{st['drop']} "
              f"(キャッシュ命中{st['cached']} 未判定{st.get('unjudged', 0)} "
              f"判定失敗{st.get('failed', 0)})")
    if sem_cache:
        print(f"意味類似キャッシュ: 候補{replay_stats['cand']} 再適用{replay_stats['kept']} "
              f"(ID不一致でスキップ{replay_stats['mismatch']}法案)")
    print(f"\n紐付け {total_attached} 件")
    if sem_authoritative and not args.dry and not args.no_semantic_cache:
        entries = build_semantic_cache(bills, suppressed, gate)
        save_semantic_cache(entries)
        print(f"意味類似キャッシュを更新: {SEMANTIC_CACHE}（{len(entries)}法案）")
    if not args.dry:
        json.dump(bills, open(args.infile, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        with open("data_collected.js", "w", encoding="utf-8") as f:
            f.write(render_data_js(bills))
        print(f"出力: {args.infile} / data_collected.js")


if __name__ == "__main__":
    main()
