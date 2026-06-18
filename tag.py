# -*- coding: utf-8 -*-
"""
タグ付与（検索利便性向上）
法案レコードに検索用タグを付ける。collect→match の後に実行する。

タグの出所:
  - ステータス（成立／審議中／継続審査）
  - 所管省庁
  - 件名の識別的な法令名（金融商品取引法 等）
  - 概要(要約)から抽出した主題キーワード（本人確認・ワクチン 等）

  python tag.py            # bills.json にタグを付与し data_collected.js を再出力
"""
import json, re
import match_refs as M

MAX_TAGS = 8


def make_tags(bill):
    tags = []
    if bill.get("status"):
        tags.append(bill["status"])
    m = bill.get("ministry") or ""
    if m and not m.startswith("—"):
        tags.append(m)
    # 件名の識別的な法令名（短い法令名のみ。助詞始まりの断片や全文相当のcoreは除外）
    _, terms = M.bill_terms(bill["title"])
    HIRA = set("はがをにのへとでもや")
    法名 = []
    for t in terms:
        t = re.sub(r"等$", "", t)                 # 末尾「等」を除去（重複防止）
        if 4 <= len(t) <= 12 and t[0] not in HIRA:
            法名.append(t)
    tags += sorted(set(法名), key=len)[:2]
    # 概要(要約)からの主題キーワード（長い順に）
    kws = sorted(M.keywords_from_purpose(bill.get("summary")), key=len, reverse=True)
    tags += kws[:6]
    # 重複除去（順序維持）・上限
    seen, out = set(), []
    for t in tags:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:MAX_TAGS]


def main():
    bills = json.load(open("bills.json", encoding="utf-8"))
    for b in bills:
        b["tags"] = make_tags(b)
    json.dump(bills, open("bills.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    with open("data_collected.js", "w", encoding="utf-8") as f:
        f.write("window.BILLS = ")
        json.dump(bills, f, ensure_ascii=False, indent=2)
        f.write(";\n")
    n = sum(len(b["tags"]) for b in bills)
    print(f"タグ付与: {len(bills)}法案 / 計{n}タグ")
    for b in bills[:3]:
        print(f"  {b['no']}: {b['tags']}")


if __name__ == "__main__":
    main()
