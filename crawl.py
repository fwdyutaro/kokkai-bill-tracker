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
from urllib.parse import urljoin
import requests, yaml, feedparser
from bs4 import BeautifulSoup

DB = "refs.db"
UA = {"User-Agent": "bill-tracker/0.1 (research; contact: example@example.com)"}


def db_init(con):
    con.execute("""CREATE TABLE IF NOT EXISTS documents(
        id TEXT PRIMARY KEY, source_id TEXT, tier INTEGER, category TEXT,
        publisher TEXT, title TEXT, url TEXT, published_at TEXT, ministry TEXT,
        content_hash TEXT, fetched_at TEXT)""")
    con.commit()


def doc_id(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


PID_RE = re.compile(r"pid/(\d+)")


def canon(url):
    """国会図書館の各種URL（prepareDownload等）を恒久リンク dl.ndl.go.jp/pid/<pid> に正規化。"""
    m = PID_RE.search(url or "")
    return f"https://dl.ndl.go.jp/pid/{m.group(1)}" if m else url


def upsert(con, src, title, url, published_at, ministry=None):
    url = canon(url)
    title = re.sub(r"\s+", " ", title or "").strip()
    if not title or not url:
        return 0
    h = hashlib.sha1((title + url).encode("utf-8")).hexdigest()[:12]
    con.execute("""INSERT INTO documents
        (id,source_id,tier,category,publisher,title,url,published_at,ministry,content_hash,fetched_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET title=excluded.title,
          published_at=excluded.published_at, content_hash=excluded.content_hash,
          fetched_at=excluded.fetched_at""",
        (doc_id(url), src["id"], src["tier"], src["category"], src["publisher"],
         title, url, published_at, ministry or src.get("ministry"), h,
         datetime.now().isoformat(timespec="seconds")))
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
    mode=links: 一覧から会議名リンクを抽出（総務省など）
    mode=page : ページ自体を1会議として扱い、報告書PDFも収録（警察庁など）
    """
    soup = BeautifulSoup(get_html(src["url"]), "html.parser")
    mode = src.get("mode", "links")
    n = 0
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
                n += upsert(con, src, f"{main}（報告書・{t}）", href, "", src.get("ministry"))
    else:
        for a in soup.find_all("a", href=True):
            t = a.get_text(" ", strip=True)
            if len(t) >= 8 and MEETING_KW.search(t) and "一覧" not in t and "はこちら" not in t:
                n += upsert(con, src, t, urljoin(src["url"], a["href"]), "", src.get("ministry"))
    return n


PARSERS = {"rss": crawl_rss, "ndl_archive": crawl_ndl_archive,
           "sangiin_rippou": crawl_sangiin_rippou,
           "ministry_index": crawl_ministry_index}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    con = sqlite3.connect(DB)
    db_init(con)

    if args.stats:
        for row in con.execute("""SELECT publisher, COUNT(*), MAX(published_at)
                                  FROM documents GROUP BY publisher"""):
            print(f"  {row[0]}: {row[1]}件 (最新 {row[2]})")
        print("  合計:", con.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        return

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


if __name__ == "__main__":
    main()
