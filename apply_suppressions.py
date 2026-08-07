# -*- coding: utf-8 -*-
"""
取り下げ（除外）の適用
suppressions.json（承認済みの取り下げ依頼）に基づき、法案レコードから該当refを除去する。
collect→match→merge_submissions の後、tag の前に毎回実行することで、
ビルドで refs が作り直されても取り下げが維持される。

suppressions.json 形式: [{"bill_no":"閣法 第31号","url":"https://...","status":"approved"}]

  python apply_suppressions.py
"""
import json, os, re
from data_output import render_data_js

SUP = "suppressions.json"


def load():
    if os.path.exists(SUP):
        try:
            return json.load(open(SUP, encoding="utf-8"))
        except Exception:
            return []
    return []


_BILL_NO_RE = re.compile(r"(閣法|衆法|参法)\s*第?\s*(\d+)\s*号")


def _bill_key(value):
    m = _BILL_NO_RE.search(str(value or ""))
    return (m.group(1), int(m.group(2))) if m else None


def main():
    sup = [s for s in load() if s.get("status") == "approved"]
    # (bill_no, url) の集合。bills.json は複数会期を保持するため議案番号は
    # 会期をまたいで衝突しうるが、urlまで一致する必要があるので実害はない
    # （同一URLが別会期の同番号法案にも紐付いていれば、そちらも除外される）。
    deny_ids = {(s.get("bill_id"), s.get("url")) for s in sup if s.get("bill_id") and s.get("url")}
    deny_legacy = {(_bill_key(s.get("bill_no")), s.get("url")) for s in sup if not s.get("bill_id") and s.get("url")}
    bills = json.load(open("bills.json", encoding="utf-8"))
    removed = 0
    for b in bills:
        before = len(b["refs"])
        bkey = _bill_key(b.get("no"))
        # An unknown bill_id must never fall back to bill_no; legacy entries
        # apply only when exactly one bill has that number.
        legacy_count = sum(1 for x in bills if _bill_key(x.get("no")) == bkey)
        b["refs"] = [r for r in b["refs"] if not (
            (b.get("id"), r.get("url")) in deny_ids or
            (legacy_count == 1 and (bkey, r.get("url")) in deny_legacy)
        )]
        removed += before - len(b["refs"])
    json.dump(bills, open("bills.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    with open("data_collected.js", "w", encoding="utf-8") as f:
        f.write(render_data_js(bills))
    print(f"取り下げ適用: {removed}件 除外（承認済 {len(sup)}件）")


if __name__ == "__main__":
    main()
