# -*- coding: utf-8 -*-
"""
国会 法律案トラッカー — 収集スクリプト
参議院「議案情報」(第N回国会) をスクレイピングして、正規化済みJSONを出力する。

参議院の議案明細ページは衆参両院の審議経過を1ページに含むため、これを主軸にする。
出力:
  - bills.json        … 正規化済みデータ（汎用）
  - data_collected.js … プロトタイプサイト用 (window.BILLS = [...])

使い方:
  python collect.py            # 第221回・全法律案
  python collect.py --diet 221 --only 31,32,33,13   # 議案番号で絞り込み
  python collect.py --limit 5  # 先頭5件だけ（動作確認用）
"""
import argparse, json, re, sys, time
from urllib.parse import urljoin, quote
import requests
from bs4 import BeautifulSoup
import enrich as clb

KOKKAI_API = "https://kokkai.ndl.go.jp/api/meeting_list"


def kokkai_keyword(title):
    """会議録検索用の簡潔キーワードを件名から抽出（〇〇法、無ければ先頭の内容語）。"""
    t = re.sub(r"(の一部を改正する等の法律案|の一部を改正する法律案|法律案|案)$", "", title)
    m = re.search(r"([一-龥ァ-ヴー]{2,14}法)(?:及び|等|の一部|に関する|施行|$)", t)
    if m:
        return m.group(1)
    m = re.match(r"([^のにをはがと、　]{3,12})", t)
    return m.group(1) if m else t[:10]


def kokkai_refs(title, diet, sections):
    """法案名＋会期＋（付託委員会／本会議）で国会会議録検索APIを叩くリンクを生成。"""
    diet_no = re.sub(r"\D", "", diet)
    kw = kokkai_keyword(title)
    meetings = []
    for k in ("衆議院委員会等経過", "参議院委員会等経過"):
        c = sections.get(k, {}).get("付託委員会等")
        if c and c not in meetings:
            meetings.append(c)
    if not meetings:               # 付託委員会が無い（委員長提出等）は本会議
        meetings = ["本会議"]
    # nameOfMeeting は1値ずつ（複数指定はORにならないため会議ごとにリンク）
    refs = []
    for m in meetings:
        url = (f"{KOKKAI_API}?sessionFrom={diet_no}&sessionTo={diet_no}"
               f"&nameOfMeeting={quote(m)}&any={quote(kw)}&maximumRecords=30")
        refs.append({"tier": 1, "cat": "会議録", "pub": "国立国会図書館",
                     "title": f"会議録検索（第{diet_no}回・{m}／「{kw}」）", "url": url})
    return refs

BASE = "https://www.sangiin.go.jp/japanese/joho1/kousei/gian/{diet}/gian.htm"
UA = {"User-Agent": "bill-tracker/0.1 (research; contact: example@example.com)"}
SESSION = requests.Session()
SESSION.headers.update(UA)

# 法律案のセクション見出し → 議案種別
LAW_SECTIONS = {
    "法律案（内閣提出）": "閣法",
    "法律案（衆法）": "衆法",
    "法律案（参法）": "参法",
}


def fetch(url):
    """UTF-8前提でデコード（参議院サイトは現在UTF-8）。失敗時はcp932/euc-jpを試す。"""
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    raw = r.content
    for enc in ("utf-8", "cp932", "euc-jp"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def wareki_to_iso(s):
    """'令和8年3月24日' → '2026-03-24'。変換できなければ None。"""
    if not s:
        return None
    m = re.search(r"(令和|平成|昭和)\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", s)
    if not m:
        return None
    era, y, mo, d = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    base = {"令和": 2018, "平成": 1988, "昭和": 1925}[era]
    return f"{base + y:04d}-{mo:02d}-{d:02d}"


def discover_bills(diet):
    """一覧ページから法律案(閣法/衆法/参法)の (種別, 件名, URL) を抽出。"""
    index_url = BASE.format(diet=diet)
    soup = BeautifulSoup(fetch(index_url), "html.parser")
    current = None
    out = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "a"]):
        if el.name in ("h1", "h2", "h3", "h4"):
            label = el.get_text(strip=True)
            current = next((v for k, v in LAW_SECTIONS.items() if k in label), None)
        elif el.name == "a" and current and "meisai" in (el.get("href") or ""):
            title = el.get_text(strip=True)
            if title:
                out.append({
                    "type": current,
                    "title": title,
                    "url": urljoin(index_url, el["href"]),
                })
    return out


def table_kv(table):
    """1テーブルを {ラベル: 値} の辞書化（複数列はラベル/値が交互に並ぶ前提）。"""
    kv, section = {}, None
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        texts = [c.get_text(" ", strip=True) for c in cells]
        if len(texts) == 1 and texts[0]:
            section = texts[0]            # 「参議院委員会等経過」等のセクション見出し行
        else:
            for i in range(0, len(texts) - 1, 2):
                k, v = texts[i], texts[i + 1]
                if k:
                    kv[k] = v
    return section, kv


def parse_bill(url):
    """議案明細ページを解析して生フィールドを返す。"""
    soup = BeautifulSoup(fetch(url), "html.parser")
    data = {"url": url, "sections": {}, "pdf": None}
    for t in soup.find_all("table"):
        section, kv = table_kv(t)
        if section:
            data["sections"][section] = kv
        else:
            data.setdefault("head", {}).update(kv)
        # PDFリンク（提出法律案）
        for a in t.find_all("a", href=True):
            if a["href"].lower().endswith(".pdf"):
                data["pdf"] = urljoin(url, a["href"])
    # 件名・種別・番号は最初のテーブル（headに入る）
    head = data.get("head", {})
    data["title"] = head.get("件名", "")
    data["kind_raw"] = head.get("種別", "")
    data["diet"] = head.get("提出回次", "")
    data["no"] = head.get("提出番号", "")
    return data


STAGE_RANK = {"提出": 0, "衆委付託": 1, "衆委議決": 2, "衆本": 3,
              "参受領": 4, "参委付託": 5, "参委議決": 6, "参本": 7, "公布": 9}


def build_timeline(d):
    """生フィールドから時系列イベントを合成。先議区分で衆参の順を決める。"""
    s = d["sections"]
    head = d.get("head", {})
    ev = []

    def add(date, house, event, result="", rank=0):
        iso = wareki_to_iso(date)
        if iso:
            ev.append({"date": iso, "house": house, "event": event,
                       "result": result, "_rank": rank})

    add(head.get("提出日"), "提出", "国会に法律案を提出", rank=0)

    def house_block(prefix, com_key, hon_key, recv=None):
        com = s.get(com_key, {})
        hon = s.get(hon_key, {})
        if recv:
            add(head.get(recv), prefix, f"{prefix}議院 受領／送付", rank=STAGE_RANK.get(prefix+"受領", 4))
        add(com.get("本付託日"), prefix, f"{com.get('付託委員会等','委員会')}に付託",
            rank=STAGE_RANK.get(prefix+"委付託", 1))
        add(com.get("議決日"), prefix, f"{com.get('付託委員会等','委員会')} 議決",
            com.get("議決・継続結果", ""), rank=STAGE_RANK.get(prefix+"委議決", 2))
        res = hon.get("議決", "")
        # 採決方法セルには「（…投票結果はこちら）」等のリンク文言が混入するため除去
        clean = lambda v: re.split(r"[（(]", v or "", 1)[0].strip()
        extra = "・".join(x for x in [clean(hon.get("採決態様")), clean(hon.get("採決方法"))] if x)
        add(hon.get("議決日"), prefix, f"本会議 採決{('（'+extra+'）') if extra else ''}",
            res, rank=STAGE_RANK.get(prefix+"本", 3))

    sengi = head.get("先議区分", "")
    if "参先議" in sengi:
        house_block("参", "参議院委員会等経過", "参議院本会議経過")
        house_block("衆", "衆議院委員会等経過", "衆議院本会議経過", recv="衆議院から受領／提出日")
    else:  # 既定: 衆先議
        house_block("衆", "衆議院委員会等経過", "衆議院本会議経過")
        house_block("参", "参議院委員会等経過", "参議院本会議経過", recv="衆議院から受領／提出日")

    other = s.get("その他", {})
    promu = other.get("公布年月日")
    lawno = other.get("法律番号")
    if wareki_to_iso(promu):
        add(promu, "公布", f"公布（法律第{lawno}号）" if lawno else "公布", "成立", rank=9)

    ev.sort(key=lambda x: (x["date"], x["_rank"]))
    for e in ev:
        e.pop("_rank", None)
    return ev, promu, lawno


def derive_status(d, timeline, promu):
    """成立 / 継続審査 / 廃案 / 審議中 を判定し、ステータス詳細も返す。"""
    s = d["sections"]
    head = d.get("head", {})
    results = [s.get(k, {}).get("議決・継続結果", "") for k in
               ("衆議院委員会等経過", "参議院委員会等経過")]
    results += [s.get(k, {}).get("議決", "") for k in
                ("衆議院本会議経過", "参議院本会議経過")]
    blob = " ".join(results) + head.get("継続区分", "")

    if wareki_to_iso(promu):
        lawno = s.get("その他", {}).get("法律番号", "")
        return "成立", f"公布済（法律第{lawno}号）" if lawno else "公布済"
    if "継続" in blob:
        return "継続審査", "継続審査中"
    if "否決" in blob or "廃案" in blob:
        return "廃案", "否決・廃案"
    # 審議中: 直近イベントから現在地を作る
    last = timeline[-1] if timeline else None
    detail = "審議中"
    if last:
        h, e, r = last["house"], last["event"], last["result"]
        if "本会議" in e and r:
            detail = f"{h}・本会議{r}（次院へ）"
        elif "本会議" in e:
            detail = f"{h}・本会議採決待ち"
        elif "議決" in e and r == "可決":
            detail = f"{h}・本会議採決待ち"        # 委員会可決済み
        elif "付託" in e:
            detail = f"{h}・委員会審査中"
        elif "受領" in e or "送付" in e:
            detail = f"{h}・委員会付託待ち"
        elif h == "提出":
            detail = "提出（付託前）"
    return "審議中", detail


def normalize(raw, type_hint=None):
    timeline, promu, lawno = build_timeline(raw)
    status, status_detail = derive_status(raw, timeline, promu)
    kind = type_hint or ("閣法" if "内閣提出" in raw["kind_raw"] else
                         "衆法" if "衆" in raw["kind_raw"] else
                         "参法" if "参" in raw["kind_raw"] else "")
    head = raw.get("head", {})
    submitted = wareki_to_iso(head.get("提出日"))
    refs = [
        {"tier": 1, "cat": "一次資料", "pub": "参議院",
         "title": "議案情報（審議経過）", "url": raw["url"]},
    ]
    if raw.get("pdf"):
        refs.append({"tier": 1, "cat": "一次資料", "pub": "参議院",
                     "title": "提出法律案（PDF）", "url": raw["pdf"]})
    refs += kokkai_refs(raw["title"], raw["diet"], raw["sections"])
    return {
        "id": f"{raw['diet'].replace('回','')}-{kind}-{raw['no']}",
        "diet": raw["diet"],
        "dietLabel": f"第{raw['diet']}",
        "no": f"{kind} 第{raw['no']}号",
        "type": kind,
        "title": raw["title"],
        "shortTitle": raw["title"][:40] + ("…" if len(raw["title"]) > 40 else ""),
        "ministry": "—",
        "submittedOn": submitted or "",
        "status": status,
        "statusDetail": status_detail,
        "confidence": {"成立": 100, "審議中": 60, "継続審査": 25, "廃案": 0}.get(status, 50),
        "summary": "（提案理由・趣旨から自動要約する想定。本スクリプトでは未取得）",
        "summaryNote": "AI要約は別工程で付与（一次資料で要確認）",
        "tags": [],
        "timeline": timeline,
        "refs": refs,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--diet", default="221")
    ap.add_argument("--only", default="", help="議案番号をカンマ区切りで指定")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=1.0, help="リクエスト間隔(秒)")
    ap.add_argument("--no-enrich", action="store_true",
                    help="内閣法制局による省庁名・提出理由の補完を行わない")
    ap.add_argument("--clb-id", default=None, help="内閣法制局 一覧ページID（新会期用）")
    ap.add_argument("--llm-summary", action="store_true",
                    help="提出理由をLLMで3行要約（要 ANTHROPIC_API_KEY）")
    args = ap.parse_args()

    only = set(x.strip() for x in args.only.split(",") if x.strip())
    print(f"[1/3] 一覧取得: 第{args.diet}回国会 ...", file=sys.stderr)
    bills = discover_bills(args.diet)
    print(f"      法律案 {len(bills)} 件を検出", file=sys.stderr)

    records = []
    for i, b in enumerate(bills):
        no = b["url"].rsplit(".", 1)[0][-3:].lstrip("0")
        if only and no not in only:
            continue
        if args.limit and len(records) >= args.limit:
            break
        print(f"[2/3] 解析 {b['type']} {no}: {b['title'][:30]} ...", file=sys.stderr)
        try:
            raw = parse_bill(b["url"])
            rec = normalize(raw, type_hint=b["type"])
            records.append(rec)
        except Exception as e:
            print(f"      ! 失敗: {e}", file=sys.stderr)
        time.sleep(args.sleep)

    if not args.no_enrich:
        print("[2.5/3] 内閣法制局: 省庁名・提出理由を補完 ...", file=sys.stderr)
        clb.enrich(records, args.diet, clb_id=args.clb_id,
                   use_llm=args.llm_summary, sleep=max(args.sleep, 0.3))

    with open("bills.json", "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    with open("data_collected.js", "w", encoding="utf-8") as f:
        f.write("window.BILLS = ")
        json.dump(records, f, ensure_ascii=False, indent=2)
        f.write(";\n")
    print(f"[3/3] 出力: bills.json / data_collected.js（{len(records)}件）", file=sys.stderr)


if __name__ == "__main__":
    main()
