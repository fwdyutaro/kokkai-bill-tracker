# -*- coding: utf-8 -*-
"""
リンク死活チェック（簡潔版）
bills.json の参考リンクURLを HEAD で確認し、死んでいるものを報告。
任意で Wayback Machine の保存版URLを提案する。

  python linkcheck.py            # 全URLを確認
  python linkcheck.py --sample 80
  python linkcheck.py --wayback  # 死リンクにWayback代替を表示
"""
import argparse, json, sys
import requests

UA = {"User-Agent": "bill-tracker-linkcheck/0.1"}


def alive(url):
    try:
        r = requests.head(url, timeout=15, headers=UA, allow_redirects=True)
        if r.status_code >= 400 or r.status_code == 405:  # 一部はHEAD非対応
            r = requests.get(url, timeout=15, headers=UA, stream=True)
        return r.status_code < 400, r.status_code
    except Exception as e:
        return False, str(e)[:40]


def wayback(url):
    try:
        r = requests.get("http://archive.org/wayback/available",
                         params={"url": url}, timeout=15, headers=UA)
        snap = r.json().get("archived_snapshots", {}).get("closest", {})
        return snap.get("url")
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", default="bills.json")
    ap.add_argument("--sample", type=int, default=0, help="先頭N件のみ確認")
    ap.add_argument("--wayback", action="store_true")
    args = ap.parse_args()

    bills = json.load(open(args.infile, encoding="utf-8"))
    urls = []
    seen = set()
    for b in bills:
        for r in b["refs"]:
            if r["url"] not in seen and r["url"].startswith("http"):
                seen.add(r["url"])
                urls.append(r["url"])
    if args.sample:
        urls = urls[:args.sample]

    dead = []
    for i, u in enumerate(urls, 1):
        ok, code = alive(u)
        if not ok:
            dead.append((u, code))
        print(f"\r  {i}/{len(urls)} 確認中 (dead {len(dead)})", end="", file=sys.stderr)
    print(file=sys.stderr)

    print(f"確認 {len(urls)} 件 / 死リンク {len(dead)} 件")
    for u, code in dead:
        print(f"  ✗ [{code}] {u}")
        if args.wayback:
            wb = wayback(u)
            if wb:
                print(f"      ↪ Wayback: {wb}")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
