# -*- coding: utf-8 -*-
"""
参考リンク収集 — クローラ／インデクサ（ステップ1: 立法補佐機関）
sources.yaml の各ソースを巡回し、文書(タイトル・URL・発行日)を refs.db に蓄積する。
本文は保存しない（タイトル＋URLのみ。著作権・規約配慮）。

  python crawl.py            # 全ソースを巡回し refs.db を更新
  python crawl.py --stats    # 蓄積状況を表示
"""
import argparse, hashlib, re, sqlite3, sys, time
from datetime import datetime
from urllib.parse import urljoin, urlparse
import requests, yaml, feedparser
from bs4 import BeautifulSoup

DB = "refs.db"
UA = {"User-Agent": "bill-tracker/0.1 (research; https://github.com/fwdyutaro/kokkai-bill-tracker)"}


def db_init(con):
    con.execute("""CREATE TABLE IF NOT EXISTS documents(
        id TEXT PRIMARY KEY, source_id TEXT, tier INTEGER, category TEXT,
        publisher TEXT, title TEXT, url TEXT, published_at TEXT, ministry TEXT,
        content_hash TEXT, fetched_at TEXT)""")
    try:  # 要旨列（PDF冒頭から抽出。意味マッチとLLM判定の材料）
        con.execute("ALTER TABLE documents ADD COLUMN abstract TEXT")
    except sqlite3.OperationalError:
        pass  # 追加済み
    try:  # 報告書PDFが属する会議ページのURL（会議ページと報告書を組で扱うため）
        con.execute("ALTER TABLE documents ADD COLUMN parent_url TEXT")
    except sqlite3.OperationalError:
        pass  # 追加済み
    con.commit()


def doc_id(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


PID_RE = re.compile(r"pid/(\d+)")


def canon(url):
    """国会図書館の各種URL（prepareDownload等）を恒久リンク dl.ndl.go.jp/pid/<pid> に正規化。"""
    m = PID_RE.search(url or "")
    return f"https://dl.ndl.go.jp/pid/{m.group(1)}" if m else url


def upsert(con, src, title, url, published_at, ministry=None, parent_url=None):
    url = canon(url)
    title = re.sub(r"\s+", " ", title or "").strip()
    if not title or not url:
        return 0
    h = hashlib.sha1((title + url).encode("utf-8")).hexdigest()[:12]
    con.execute("""INSERT INTO documents
        (id,source_id,tier,category,publisher,title,url,published_at,ministry,content_hash,
         fetched_at,parent_url)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET title=excluded.title,
          published_at=excluded.published_at, content_hash=excluded.content_hash,
          fetched_at=excluded.fetched_at,
          parent_url=COALESCE(excluded.parent_url, documents.parent_url)""",
        (doc_id(url), src["id"], src["tier"], src["category"], src["publisher"],
         title, url, published_at, ministry or src.get("ministry"), h,
         datetime.now().isoformat(timespec="seconds"), parent_url))
    return 1


def get(url, enc=None):
    r = requests.get(url, timeout=30, headers=UA)
    r.raise_for_status()
    return r.content if enc is None else r.content.decode(enc, "replace")


def get_html(url):
    """文字コードを自動判定してHTMLテキストを返す（省庁によりUTF-8/SHIFT_JIS混在）。"""
    raw = get(url)
    for enc in ("utf-8", "cp932", "euc-jp"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


# ---- 取得方式ごとのパーサ -------------------------------------------------

# RSSタイトル末尾の「調査と情報 (1363) 2026-06-09」等を除去して主題のみに
RSS_SUFFIX = re.compile(r"\s*(調査と情報|レファレンス)\s*[\(（]\s*\d+\s*[\)）].*$")


RSS_UA = {"User-Agent": "Mozilla/5.0 (compatible; bill-tracker/0.1; research)"}


def crawl_rss(con, src):
    # UAでブロックする配信元があるためブラウザ風UAで取得してから解析
    try:
        raw = requests.get(src["url"], timeout=30, headers=RSS_UA).content
        feed = feedparser.parse(raw)
    except Exception:
        feed = feedparser.parse(src["url"])
    n = 0
    entries = feed.entries[: src["limit"]] if src.get("limit") else feed.entries
    for e in entries:
        published = ""
        if getattr(e, "published_parsed", None):
            published = time.strftime("%Y-%m-%d", e.published_parsed)
        title = RSS_SUFFIX.sub("", e.get("title", "")).strip()
        n += upsert(con, src, title, e.get("link", ""), published)
    return n


def crawl_ndl_archive(con, src):
    """国会図書館の年次アーカイブ（/diet/publication/<kind>/<year>）を巡回。"""
    n = 0
    for year in src["years"]:
        url = f"https://www.ndl.go.jp/diet/publication/{src['kind']}/{year}"
        try:
            soup = BeautifulSoup(get(url, "utf-8"), "html.parser")
        except Exception as e:
            print(f"    ! {url} skip: {e}", file=sys.stderr)
            continue
        for a in soup.find_all("a", href=True):
            if "pid/" not in a["href"]:
                continue
            title = re.sub(r"[\(（]\s*PDF[:：][^)）]*[\)）]", "", a.get_text(" ", strip=True)).strip()
            n += upsert(con, src, title, urljoin(url, a["href"]), str(year))
        time.sleep(0.5)
    return n


# PDF名 例: 20260522003.pdf -> 2026-05-22
DATE_IN_URL = re.compile(r"/(\d{4})(\d{2})(\d{2})\d*\.pdf$", re.I)


def crawl_sangiin_rippou(con, src):
    """バックナンバー索引 → 直近号ページ → 記事(タイトル＋PDF＋日付)。"""
    idx = BeautifulSoup(get(src["url"], "utf-8"), "html.parser")
    issue_urls = []
    for a in idx.find_all("a", href=True):
        href = urljoin(src["url"], a["href"])
        # 号ページは backnumber/YYYYMMDD.html
        if re.search(r"/backnumber/\d{8}\.html$", href):
            issue_urls.append(href)
    issue_urls = issue_urls[: src.get("issues", 8)]

    n = 0
    for iu in issue_urls:
        soup = BeautifulSoup(get(iu, "utf-8"), "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(iu, a["href"])
            if not href.lower().endswith(".pdf"):
                continue
            title = a.get_text(" ", strip=True)
            title = re.sub(r"（PDF file[^）]*）", "", title).strip()  # サイズ表記除去
            m = DATE_IN_URL.search(href)
            published = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""
            n += upsert(con, src, title, href, published)
        time.sleep(0.5)
    return n


MEETING_KW = re.compile(r"検討会|研究会|懇談会|審議会|協議会|委員会|ワーキング|会議|タスクフォース")


def crawl_ministry_index(con, src):
    """省庁の審議会・研究会ページを巡回。
    mode=links : 一覧から会議名リンクを抽出（総務省など）
    mode=page  : ページ自体を1会議として扱い、報告書PDFも収録（個別に登録した会議）
    mode=follow: 一覧の各会議ページまでたどり、会議名と報告書・提言PDFを収録（警察庁など）
    """
    soup = BeautifulSoup(get_html(src["url"]), "html.parser")
    mode = src.get("mode", "links")
    n = 0
    if mode == "follow":
        return crawl_council_pages(con, src, soup)
    if mode == "page":
        # 会議名(h1) ＋ 部会/WG名(h2/h3) を内容語として個別にインデックス化。
        # 抽象的な会議名でも、配下WG名(例「不適正利用対策WG」)が主題語を含むため拾える。
        names = []
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            names.append(h1.get_text(" ", strip=True).split("｜")[0])
        for h in soup.find_all(["h2", "h3"]):
            t = h.get_text(" ", strip=True)
            if len(t) >= 8 and MEETING_KW.search(t) and t not in names and "総務省" not in t:
                names.append(t)
        for i, nm in enumerate(names):
            url = src["url"] if i == 0 else f"{src['url']}#sec{i}"
            n += upsert(con, src, nm, url, "", src.get("ministry"))
        main = names[0] if names else ""
        for a in soup.find_all("a", href=True):
            t = a.get_text(" ", strip=True)
            href = urljoin(src["url"], a["href"])
            if href.lower().endswith(".pdf") and ("報告" in t or "houkoku" in href.lower() or t in ("本文", "概要")):
                n += upsert(con, src, f"{main}（報告書・{t}）", href, "", src.get("ministry"),
                            parent_url=src["url"])
    else:
        for a in soup.find_all("a", href=True):
            t = a.get_text(" ", strip=True)
            if len(t) >= 8 and MEETING_KW.search(t) and "一覧" not in t and "はこちら" not in t:
                n += upsert(con, src, t, urljoin(src["url"], a["href"]), "", src.get("ministry"))
    return n


# 報告書・提言等（会議の成果物）とみなすPDFの見出し語。議事次第・資料・議事概要は対象外。
REPORT_KW = re.compile(r"報告|提言|取りまとめ|とりまとめ|中間整理|最終整理|答申|意見書")
REPORT_EXCLUDE = re.compile(r"議事|資料|構成員|名簿|開催要綱|次第")
# 見出し末尾のファイルサイズ表記（例: 報告書（544KB）／概要 (35KB)）
FILE_SIZE = re.compile(r"[（(]\s*(?:PDF\s*[:：]?\s*)?[\d.,]+\s*[KMG]i?B\s*[)）]", re.I)
# 「第１回検討会」等の各回の会合（会議そのものではない）
MEETING_NO = re.compile(r"^第\s*[0-9０-９一二三四五六七八九十]+\s*回")


def is_council_heading(text):
    """会議ページ内の見出しが会議名（部会・WG・期ごとの検討会）か。各回の会合は除く。"""
    return len(text) >= 8 and bool(MEETING_KW.search(text)) and not MEETING_NO.match(text)


def council_links(soup, base_url):
    """会議一覧ページから、同じサイト内の会議ページへのリンク (会議名, URL) を返す。"""
    host = urlparse(base_url).netloc
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        t = a.get_text(" ", strip=True)
        href = urljoin(base_url, a["href"]).split("#")[0]
        if (len(t) < 6 or not MEETING_KW.search(t) or "一覧" in t
                or href.lower().endswith(".pdf") or urlparse(href).netloc != host
                or href == base_url or href in seen):
            continue
        seen.add(href)
        out.append((t, href))
    return out


def report_pdfs(soup, page_url):
    """会議ページから報告書・提言等のPDFを (見出し, URL, リンク要素) で返す。

    リンク文字が「本文」「概要」だけのことが多い（例: 提言［本文／概要］）ため、
    リンクを含む段落の、リンクより前の文字列を見出しとして使う。
    """
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"])
        if not href.lower().endswith(".pdf") or href in seen:
            continue
        text = a.get_text(" ", strip=True)
        block = a.find_parent(["p", "li", "td", "dd", "dt"])
        lead = ""
        if block:
            lead = re.split(r"[［\[（(【]", block.get_text("", strip=True), 1)[0].strip()
            if lead == text:
                lead = ""
        label = text if REPORT_KW.search(text) or not lead else f"{lead}・{text}"
        label = re.sub(r"\s+", "", FILE_SIZE.sub("", label))
        if not REPORT_KW.search(label) or REPORT_EXCLUDE.search(label):
            continue
        seen.add(href)
        out.append((label, href, a))
    return out


def crawl_council_pages(con, src, index_soup):
    """mode=follow: 一覧 → 各会議ページ → 会議名・部会名・報告書PDF。"""
    n = 0
    for name, url in council_links(index_soup, src["url"]):
        try:
            soup = BeautifulSoup(get_html(url), "html.parser")
        except Exception as e:
            print(f"    ! {url} skip: {e}", file=sys.stderr)
            continue
        # 会議名は一覧のリンク文字を優先（会議ページの h1 は「有識者検討会」等と短いことがある）
        n += upsert(con, src, name, url, "", src.get("ministry"))
        # 1ページに複数の期・部会が並ぶ場合（例: 令和8年～／令和2年～3年の検討会）、
        # h2/h3 の会議名を個別の文書（ページ内アンカー）として登録する。
        sections = {}
        for i, h in enumerate(soup.find_all(["h2", "h3"]), 1):
            t = h.get_text(" ", strip=True)
            if is_council_heading(t) and t != name:
                sections[id(h)] = (t, f"{url}#sec{i}")
                n += upsert(con, src, t, f"{url}#sec{i}", "", src.get("ministry"))
        for label, href, a in report_pdfs(soup, url):
            # 報告書は直前の h2 の会議に属するとみなす。h2 が会議名でない区画（例: 調査研究）は
            # その見出しを名前にし、紐付け先はページ全体の会議とする。
            h2 = a.find_previous("h2")
            owner, owner_url = name, url
            if h2 is not None:
                h2_text = h2.get_text(" ", strip=True)
                owner, owner_url = sections.get(id(h2), (h2_text or name, url))
            n += upsert(con, src, f"{owner}（{label}）", href, "", src.get("ministry"),
                        parent_url=owner_url)
        time.sleep(src.get("sleep", 0.5))
    return n


PARSERS = {"rss": crawl_rss, "ndl_archive": crawl_ndl_archive,
           "sangiin_rippou": crawl_sangiin_rippou,
           "ministry_index": crawl_ministry_index}


# ---- 要旨抽出（PDF冒頭テキスト） -------------------------------------------
# 立法と調査: url が直接PDF。NDLデジコレ: pid から prepareDownload でPDF取得。
NDL_PID = re.compile(r"dl\.ndl\.go\.jp/pid/(\d+)")


def _pdf_url(doc_url):
    if doc_url.lower().endswith(".pdf"):
        return doc_url
    m = NDL_PID.search(doc_url)
    if m:
        return f"https://dl.ndl.go.jp/view/prepareDownload?itemId=info:ndljp/pid/{m.group(1)}"
    return None


def _extract_first_page(pdf_bytes, max_chars=700):
    from io import BytesIO
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(pdf_bytes))
    txt = ""
    for page in reader.pages[:2]:
        txt += (page.extract_text() or "") + " "
        if len(txt) >= max_chars:
            break
    return re.sub(r"\s+", " ", txt).strip()[:max_chars]


def enrich_abstracts(con, sleep=0.6, limit=0):
    """abstract未取得の対象文書(NDL・立法と調査)のPDF冒頭を抽出して保存。増分実行。"""
    rows = con.execute(
        """SELECT id,url FROM documents WHERE abstract IS NULL AND
           (url LIKE '%dl.ndl.go.jp/pid/%' OR url LIKE '%.pdf')""").fetchall()
    if limit:
        rows = rows[:limit]
    ok = fail = 0
    for i, (doc_id_, url) in enumerate(rows, 1):
        pdf = _pdf_url(url)
        text = ""
        if pdf:
            try:
                r = requests.get(pdf, timeout=40, headers=UA)
                if r.status_code == 200 and r.content[:4] == b"%PDF":
                    text = _extract_first_page(r.content)
            except Exception:
                pass
        # 失敗は '' を保存（IS NULL条件から外し、毎回の再試行を防ぐ）
        con.execute("UPDATE documents SET abstract=? WHERE id=?", (text, doc_id_))
        ok += bool(text); fail += not text
        if i % 20 == 0:
            con.commit()
            print(f"    要旨 {i}/{len(rows)} (成功{ok})", file=sys.stderr)
        time.sleep(sleep)
    con.commit()
    print(f"  [abstracts] 対象{len(rows)}件 成功{ok} 失敗{fail}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--no-abstracts", action="store_true", help="要旨(PDF冒頭)抽出を行わない")
    ap.add_argument("--abstracts-only", action="store_true", help="巡回せず要旨抽出のみ")
    ap.add_argument("--no-report-laws", action="store_true",
                    help="報告書PDFの法令名抽出（report_laws.json の更新）を行わない")
    args = ap.parse_args()
    con = sqlite3.connect(DB)
    db_init(con)

    if args.stats:
        for row in con.execute("""SELECT publisher, COUNT(*), MAX(published_at)
                                  FROM documents GROUP BY publisher"""):
            print(f"  {row[0]}: {row[1]}件 (最新 {row[2]})")
        print("  合計:", con.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        return

    if not args.abstracts_only:
        cfg = yaml.safe_load(open("sources.yaml", encoding="utf-8"))
        total = 0
        for src in cfg["sources"]:
            parser = PARSERS.get(src["method"])
            if not parser:
                print(f"  ! 未対応の method: {src['method']}", file=sys.stderr)
                continue
            try:
                n = parser(con, src)
                con.commit()
                print(f"  [{src['id']}] {n}件 取り込み", file=sys.stderr)
                total += n
            except Exception as e:
                print(f"  ! {src['id']} 失敗: {e}", file=sys.stderr)
        print(f"完了: {total}件（refs.db）", file=sys.stderr)
    if not args.no_abstracts:
        enrich_abstracts(con)
    # 報告書PDFの法令名抽出は新しいPDFだけの増分処理で軽いため、CI（--no-abstracts）でも行う。
    # 結果は report_laws.json に残り、CI と週次処理の照合で共有される。
    if not args.no_report_laws and not args.abstracts_only:
        import report_laws
        cache = report_laws.load_cache()
        if report_laws.update_cache(con, get, cache):
            report_laws.save_cache(cache)


if __name__ == "__main__":
    main()
