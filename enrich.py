# -*- coding: utf-8 -*-
"""
国会 法律案トラッカー — 補強モジュール
内閣法制局「内閣提出法律案」ページから、閣法の (1)主管省庁 と (2)提出理由(=概要の元ネタ)
を取得して付与する。

- 主管省庁: 一覧ページ id=<clb_id> の表（閣法番号・成立・法案名・主管省庁＋詳細/省庁リンク）
- 提出理由: 各法案の詳細ページ（dt/dd 形式）の「提出理由」

概要(summary)は既定で「提出理由」の公式原文を使う（ハルシネーション回避）。
ANTHROPIC_API_KEY があり --llm-summary 指定時のみ、3行の平易な要約に変換する。
"""
import os, re, time
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "bill-tracker/0.1 (research)"}

# 国会回次 → 内閣法制局 一覧ページID（会期ごとに採番。新会期は要追加 or --clb-id で指定）
CLB_INDEX = {"221": "5144"}
CLB_LIST = "https://www.clb.go.jp/recent-laws/diet_bill/id={cid}"

# 衆議院 議案一覧（種別=caption、本文リンクあり。文字コードはSHIFT_JIS）
SHU_LIST = "https://www.shugiin.go.jp/internet/itdb_gian.nsf/html/gian/kaiji{diet}.htm"
SHU_KIND = {"衆法の一覧": "衆法", "参法の一覧": "参法", "閣法の一覧": "閣法"}


def _get(url, enc="utf-8"):
    r = requests.get(url, timeout=30, headers=UA)
    r.raise_for_status()
    return r.content.decode(enc, "replace")


def build_ministry_map(diet, clb_id=None):
    """{閣法番号(str, ゼロ無し): {ministry, detail_url, ministry_url}} を返す。"""
    cid = clb_id or CLB_INDEX.get(str(diet))
    if not cid:
        return {}
    soup = BeautifulSoup(_get(CLB_LIST.format(cid=cid)), "html.parser")
    table = soup.find("table")
    if not table:
        return {}
    out = {}
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) < 4 or not cells[0].isdigit():
            continue
        no = str(int(cells[0]))
        links = [a["href"] for a in tr.find_all("a", href=True)]
        detail = next((l for l in links if "/detail/" in l), None)
        minis_url = next((l for l in links if "clb.go.jp" not in l), None)
        out[no] = {
            "ministry": cells[3] or "—",
            "detail_url": urljoin(CLB_LIST.format(cid=cid), detail) if detail else None,
            "ministry_url": minis_url,
        }
    return out


def fetch_reason(detail_url):
    """詳細ページの dt/dd から提出理由・閣議決定日・先議院を取得。"""
    if not detail_url:
        return {}
    soup = BeautifulSoup(_get(detail_url), "html.parser")
    info = {}
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        key = dt.get_text(" ", strip=True)
        if key in ("提出理由", "閣議決定日", "先議院", "主管省庁") and dd:
            info[key] = dd.get_text(" ", strip=True)
    return info


def build_shugiin_map(diet):
    """{(種別, 番号): {houan(本文URL), keika, status}} を返す（全法律案）。"""
    soup = BeautifulSoup(_get(SHU_LIST.format(diet=diet), enc="shift_jis"), "html.parser")
    out = {}
    for table in soup.find_all("table"):
        cap = table.find("caption")
        kind = SHU_KIND.get(cap.get_text(strip=True)) if cap else None
        if not kind:
            continue
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) < 6 or not cells[1].isdigit():
                continue
            honbun = keika = None
            for a in tr.find_all("a", href=True):
                href = urljoin(SHU_LIST.format(diet=diet), a["href"])
                if "/honbun/" in href:
                    honbun = href
                elif "/keika/" in href:
                    keika = href
            if honbun:
                out[(kind, str(int(cells[1])))] = {
                    "houan": honbun.replace("/honbun/", "/honbun/houan/"),
                    "keika": keika,
                    "status": cells[3],
                }
    return out


REASON_RE = re.compile(r"理由(.{15,500}?)(?:である。|ものとする。)")


def fetch_shugiin_reason(houan_url):
    """衆議院の提出時法律案（本文）末尾の『理由』を抽出。議員立法の概要に使う。"""
    if not houan_url:
        return None
    body = BeautifulSoup(_get(houan_url, enc="shift_jis"), "html.parser").get_text("\n", strip=True)
    flat = re.sub(r"[\s　]+", "", body)
    m = REASON_RE.search(flat)
    return (m.group(1).strip() + "である。") if m else None


def llm_summarize(reason_text, title, model="claude-haiku-4-5-20251001"):
    """提出理由を3行の平易な要約に変換（要 ANTHROPIC_API_KEY）。失敗時 None。"""
    if not reason_text or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=model,
            max_tokens=300,
            system=("あなたは立法情報サイトの編集者です。法律案の『提出理由』を、"
                    "一般市民向けに3行以内・平易な日本語で要約してください。"
                    "誇張や憶測を加えず、原文の事実のみに基づくこと。"),
            messages=[{"role": "user",
                       "content": f"法案名: {title}\n提出理由:\n{reason_text}"}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"      ! LLM要約失敗: {e}")
        return None


def _set_summary(rec, reason, source, use_llm):
    """概要を設定。LLM要約が使えれば平易化、無ければ原文。"""
    summ = llm_summarize(reason, rec["title"]) if use_llm else None
    if summ:
        rec["summary"], rec["summaryNote"] = summ, f"{source}をAIが要約（一次資料で要確認）"
    else:
        rec["summary"], rec["summaryNote"] = reason, f"{source}（原文）"


def _no(rec):
    return str(int(rec["no"].split("第")[1].rstrip("号"))) if "第" in rec["no"] else None


def enrich(records, diet, clb_id=None, use_llm=False, sleep=0.4):
    """正規化済みレコードを補強する。
    閣法: 内閣法制局から 主管省庁＋提出理由。衆法/参法: 衆議院本文から 理由。
    全法律案: 衆議院の本文ページを参考リンクに追加。
    """
    mmap = build_ministry_map(diet, clb_id)
    try:
        smap = build_shugiin_map(diet)
    except Exception as e:
        print(f"      ! 衆議院一覧の取得失敗: {e}")
        smap = {}
    if not mmap:
        print("      ! 内閣法制局の一覧が取得できず、省庁補完をスキップ")

    for rec in records:
        no = _no(rec)
        sinfo = smap.get((rec["type"], no), {})
        # 全法律案: 衆議院本文を一次資料リンクに追加
        if sinfo.get("houan"):
            rec["refs"].append({"tier": 1, "cat": "一次資料", "pub": "衆議院",
                                "title": "提出時法律案・本文", "url": sinfo["houan"]})

        if rec["type"] == "閣法":
            info = mmap.get(no)
            if not info:
                continue
            rec["ministry"] = info["ministry"]
            if info.get("ministry_url"):
                rec["refs"].append({"tier": 1, "cat": "所管省庁", "pub": info["ministry"],
                                    "title": "所管省庁の法案ページ", "url": info["ministry_url"]})
            if info.get("detail_url"):
                rec["refs"].append({"tier": 1, "cat": "一次資料", "pub": "内閣法制局",
                                    "title": "提案理由・概要（詳細ページ）", "url": info["detail_url"]})
            reason = fetch_reason(info.get("detail_url")).get("提出理由")
            if reason:
                _set_summary(rec, reason, "内閣法制局『提出理由』", use_llm)
            time.sleep(sleep)

        elif rec["type"] in ("衆法", "参法"):
            rec["ministry"] = "—（議員提出）"
            if sinfo.get("houan"):
                try:
                    reason = fetch_shugiin_reason(sinfo["houan"])
                    if reason:
                        _set_summary(rec, reason, "衆議院 提出法律案の『理由』", use_llm)
                except Exception as e:
                    print(f"      ! 衆議院理由の取得失敗 ({rec['no']}): {e}")
                time.sleep(sleep)
    return records


if __name__ == "__main__":
    import sys
    m = build_ministry_map("221")
    print(f"省庁マップ {len(m)} 件")
    for k in ("31", "32", "33"):
        print(" 閣法", k, "->", m.get(k, {}).get("ministry"))
    print("提出理由(閣法31):", fetch_reason(m["31"]["detail_url"]).get("提出理由", "")[:120], "...")
