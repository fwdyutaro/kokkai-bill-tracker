# -*- coding: utf-8 -*-
"""
取り下げ（除外）の適用
suppressions.json（承認済みの取り下げ依頼）に基づき、法案レコードから該当refを除去する。
collect→match→merge_submissions の後、tag の前に毎回実行することで、
ビルドで refs が作り直されても取り下げが維持される。

suppressions.json 形式: [{"bill_no":"閣法 第31号","url":"https://...","status":"approved"}]

  python apply_suppressions.py
"""
import json, os

SUP = "suppressions.json"


def load():
    if os.path.exists(SUP):
        try:
            return json.load(open(SUP, encoding="utf-8"))
        except Exception:
            return []
    return []


def main():
    sup = [s for s in load() if s.get("status") == "approved"]
    # (bill_no, url) の集合
    deny = {(s.get("bill_no"), s.get("url")) for s in sup if s.get("url")}
    bills = json.load(open("bills.json", encoding="utf-8"))
    removed = 0
    for b in bills:
        before = len(b["refs"])
        b["refs"] = [r for r in b["refs"]
                     if (b["no"], r["url"]) not in deny]
        removed += before - len(b["refs"])
    json.dump(bills, open("bills.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    with open("data_collected.js", "w", encoding="utf-8") as f:
        f.write("window.BILLS = ")
        json.dump(bills, f, ensure_ascii=False, indent=2)
        f.write(";\n")
    print(f"取り下げ適用: {removed}件 除外（承認済 {len(sup)}件）")


if __name__ == "__main__":
    main()
