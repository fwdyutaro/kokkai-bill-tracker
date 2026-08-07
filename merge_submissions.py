# -*- coding: utf-8 -*-
"""
承認済みユーザー提供情報を法案レコードに反映する。
submissions.json（承認済みレコードの配列）を bills.json の refs に取り込み、
data_collected.js を再出力する。collect→match の後（tagの前）に毎回実行することで、
ビルドで refs が作り直されても提供情報が維持される。

  python merge_submissions.py
"""
import json, os
import sessions
from data_output import render_data_js

SUB = "submissions.json"


def load_submissions():
    if os.path.exists(SUB):
        try:
            return json.load(open(SUB, encoding="utf-8"))
        except Exception:
            return []
    return []


def index_by_no(bills):
    """議案番号("閣法 第31号")→レコード。

    bills.json は複数会期を保持するため、議案番号だけでは会期をまたいで衝突する。
    Issue経由の提供情報は会期を持たないので、同じ番号が複数会期にある場合は
    最新会期のレコードを採る（利用者が見ているのは既定表示の最新会期のため）。
    """
    index = {}
    for b in bills:
        no = b.get("no")
        if not no:
            continue
        current = index.get(no)
        if current is None or _diet_of(b) >= _diet_of(current):
            index[no] = b
    return index


def _diet_of(record):
    key = sessions.normalize_diet(record.get("diet")) or \
          sessions.normalize_diet(str(record.get("id", "")).split("-", 1)[0])
    return int(key) if key else -1


def main():
    subs = [s for s in load_submissions() if s.get("status") == "approved"]
    bills = json.load(open("bills.json", encoding="utf-8"))
    by_no = index_by_no(bills)
    by_id = {b["id"]: b for b in bills if b.get("id")}
    added = 0
    for s in subs:
        # bill_id は厳密一致。未知のIDを議案番号へフォールバックしない。
        if s.get("bill_id"):
            b = by_id.get(s.get("bill_id"))
        else:
            candidates = [x for x in bills if x.get("no") == s.get("bill_no")]
            b = candidates[0] if len(candidates) == 1 else None
        if not b or not s.get("url"):
            continue
        if any(r["url"] == s["url"] for r in b["refs"]):
            continue
        b["refs"].append({
            "tier": 4, "cat": "提供情報", "pub": s.get("publisher", "—"),
            "title": s.get("title") or s["url"], "url": s["url"],
            "conf": s.get("relevance", 0), "confNote": "ユーザー提供（承認済）",
        })
        added += 1
    json.dump(bills, open("bills.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    with open("data_collected.js", "w", encoding="utf-8") as f:
        f.write(render_data_js(bills))
    print(f"提供情報を反映: {added}件（承認済 {len(subs)}件中）")


if __name__ == "__main__":
    main()
