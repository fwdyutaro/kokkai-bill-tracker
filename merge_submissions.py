# -*- coding: utf-8 -*-
"""
承認済みユーザー提供情報を法案レコードに反映する。
submissions.json（承認済みレコードの配列）を bills.json の refs に取り込み、
data_collected.js を再出力する。collect→match の後（tagの前）に毎回実行することで、
ビルドで refs が作り直されても提供情報が維持される。

  python merge_submissions.py
"""
import json, os

SUB = "submissions.json"


def load_submissions():
    if os.path.exists(SUB):
        try:
            return json.load(open(SUB, encoding="utf-8"))
        except Exception:
            return []
    return []


def main():
    subs = [s for s in load_submissions() if s.get("status") == "approved"]
    bills = json.load(open("bills.json", encoding="utf-8"))
    by_no = {b["no"]: b for b in bills}
    added = 0
    for s in subs:
        b = by_no.get(s.get("bill_no"))
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
        f.write("window.BILLS = ")
        json.dump(bills, f, ensure_ascii=False, indent=2)
        f.write(";\n")
    print(f"提供情報を反映: {added}件（承認済 {len(subs)}件中）")


if __name__ == "__main__":
    main()
