# -*- coding: utf-8 -*-
"""
情報提供（参考URL）の取り込み・関連度推定
GitHub Issue で投稿された (対象法案 + URL) を処理する中核ロジック。

- URLから書誌メタ（タイトル・概要・発行元）を抽出（og/citation/JSON-LD/title）。
- 既存のマッチャ(match_refs)を流用して法案との関連度を推定。
- 承認用のMarkdownコメントと、掲載用のJSONレコードを生成。

CLI（ローカル/Action双方で利用）:
  python ingest_submission.py --bill "閣法 第31号" --url "https://..."
  python ingest_submission.py --issue-body "$BODY" --issue-title "$TITLE"
"""
import argparse, json, re, sys
import requests
from bs4 import BeautifulSoup
import match_refs as M

UA = {"User-Agent": "Mozilla/5.0 (compatible; bill-tracker/0.1; research)"}
BILL_NO_RE = re.compile(r"(閣法|衆法|参法)\s*第?\s*(\d+)\s*号")
URL_RE = re.compile(r"https?://[^\s)>\]」]+")
GENERIC_TITLES = ("国立国会図書館デジタルコレクション", "CiNii Research", "お探しのページ")


def extract_meta(url):
    """URLからタイトル・概要・発行元を抽出。JS描画等で取れない場合は title=None。"""
    r = requests.get(url, timeout=20, headers=UA)
    soup = BeautifulSoup(r.content.decode(r.apparent_encoding or "utf-8", "replace"), "html.parser")

    def meta(*names):
        for n in names:
            t = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if t and t.get("content"):
                return t["content"].strip()
        return None

    title = meta("og:title", "citation_title", "dc.title")
    if not title:
        for ld in soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(ld.string)
                d = d[0] if isinstance(d, list) else d
                title = d.get("name") or d.get("headline")
                if title:
                    break
            except Exception:
                pass
    if not title and soup.title:
        title = soup.title.get_text(strip=True)
    # サイト名接尾辞を除去（「… | CiNii Research」「… - NHK」等）
    if title:
        title = re.split(r"\s*[|｜\-—–]\s*[^|｜\-—–]{2,30}$", title)[0].strip()
    publisher = meta("og:site_name", "citation_journal_title") or url.split("/")[2]
    desc = meta("og:description", "description", "citation_abstract") or ""
    # 取得できても汎用タイトルなら手動補完が必要
    manual = (not title) or any(g in title for g in GENERIC_TITLES)
    return {"title": title, "desc": desc[:200], "publisher": publisher,
            "status": r.status_code, "manual": manual}


def estimate_relevance(bill, title, desc=""):
    """match_refs の語彙＋趣旨キーワードで関連度(%)と根拠を推定。"""
    if not title:
        return 0, "タイトル取得不可（手動確認）"
    sc, why = M.score(bill["title"], title)
    if sc < M.ATTACH_THRESHOLD:
        pkw = M.keywords_from_purpose(bill.get("summary"))
        blob = M.norm(title + desc)
        hit = next((k for k in sorted(pkw, key=len, reverse=True) if k in blob), None)
        if hit:
            sc, why = min(0.8, 0.58 + len(hit) / 30), f"趣旨キーワード一致（{hit}）"
    return round(sc * 100), why


def find_bill(bills, bill_no_tuple):
    kind, num = bill_no_tuple
    want = f"{kind} 第{int(num)}号"
    return next((b for b in bills if b["no"] == want), None)


def process(bill_no_tuple, url, bills):
    bill = find_bill(bills, bill_no_tuple)
    if not bill:
        return None, f"対象法案が見つかりません: {bill_no_tuple}"
    meta = extract_meta(url)
    pct, why = estimate_relevance(bill, meta["title"], meta["desc"])
    rec = {"bill_no": bill["no"], "title": meta["title"] or "(要手動補完)",
           "url": url, "publisher": meta["publisher"], "relevance": pct, "why": why,
           "manual": meta["manual"], "status": "pending"}
    md = (f"### 自動処理結果\n"
          f"- **対象法案**: {bill['no']} {bill['shortTitle']}\n"
          f"- **URL**: {url}\n"
          f"- **抽出タイトル**: {meta['title'] or '（取得できず・手動補完が必要）'}\n"
          f"- **発行元**: {meta['publisher']}\n"
          f"- **推定関連度**: {pct}%（{why}）\n\n"
          + ("⚠ タイトルが自動取得できませんでした。コメントで正しいタイトルを補ってください。\n"
             if meta["manual"] else "")
          + "問題なければメンテナが `approved` ラベルを付与すると掲載されます。")
    return rec, md


def parse_issue(title, body):
    text = f"{title}\n{body}"
    bm = BILL_NO_RE.search(text)
    um = URL_RE.search(body or "")
    return (bm.group(1), bm.group(2)) if bm else None, (um.group(0) if um else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bill"); ap.add_argument("--url")
    ap.add_argument("--issue-title", default=""); ap.add_argument("--issue-body", default="")
    ap.add_argument("--out", help="JSONレコードの出力先")
    args = ap.parse_args()

    bills = json.load(open("bills.json", encoding="utf-8"))
    if args.bill and args.url:
        bn = BILL_NO_RE.search(args.bill); bnt = (bn.group(1), bn.group(2)) if bn else None
        url = args.url
    else:
        bnt, url = parse_issue(args.issue_title, args.issue_body)
    if not bnt or not url:
        print("法案番号またはURLを解釈できませんでした。", file=sys.stderr)
        print("対象法案とURLを確認してください。")
        return 2

    rec, md = process(bnt, url, bills)
    print(md)
    if rec and args.out:
        json.dump(rec, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
