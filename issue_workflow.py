# -*- coding: utf-8 -*-
"""GitHub Issue 承認ワークフローの、ローカルで検証可能な状態判定。"""

import argparse
import json
import os
import re
from pathlib import Path


BILL_NO_RE = re.compile(r"(閣法|衆法|参法)\s*第?\s*(\d+)\s*号")
URL_RE = re.compile(r"https?://[^\s)>\]」]+")


def load_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def write_json(path, value):
    with Path(path).open("w", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, indent=2)
        target.write("\n")


def parse_removal(title, body):
    text = f"{title}\n{body or ''}"
    bill_match = BILL_NO_RE.search(text)
    url_match = URL_RE.search(body or "")
    if not bill_match or not url_match:
        return None
    return f"{bill_match.group(1)} 第{int(bill_match.group(2))}号", url_match.group(0)


def approve_submission(record_path, submissions_path):
    record = load_json(record_path, None)
    if not isinstance(record, dict) or not record.get("bill_no") or not record.get("url"):
        raise ValueError("掲載レコードが生成されていないか、必須項目がありません")
    submissions = load_json(submissions_path, [])
    if not isinstance(submissions, list):
        raise ValueError("submissions.json は配列である必要があります")
    duplicate = any(item.get("url") == record["url"] for item in submissions)
    if not duplicate:
        record["status"] = "approved"
        submissions.append(record)
        write_json(submissions_path, submissions)
    return "already-listed" if duplicate else "added"


def approve_removal(title, body, bills_path, suppressions_path):
    parsed = parse_removal(title, body)
    if not parsed:
        return "manual_review", "法案番号またはURLを解釈できませんでした"
    bill_no, url = parsed
    bills = load_json(bills_path, [])
    bill = next((item for item in bills if item.get("no") == bill_no), None)
    if bill is None:
        return "manual_review", f"対象法案が見つかりません: {bill_no}"

    suppressions = load_json(suppressions_path, [])
    if not isinstance(suppressions, list):
        raise ValueError("suppressions.json は配列である必要があります")
    duplicate = any(
        item.get("bill_no") == bill_no and item.get("url") == url
        for item in suppressions
    )
    exists = any(ref.get("url") == url for ref in bill.get("refs", []))
    if not exists and not duplicate:
        return "manual_review", "指定URLは対象法案の参考リンクに存在しません"
    if not duplicate:
        suppressions.append({"bill_no": bill_no, "url": url, "status": "approved"})
        write_json(suppressions_path, suppressions)
    detail = "既に取り下げ済みです" if duplicate else "取り下げ対象を確認しました"
    return "published-ready", detail


def verify_removal(title, body, bills_path):
    """取り下げ適用後、対象URLが公開データから消えたことを確認する。"""
    parsed = parse_removal(title, body)
    if not parsed:
        raise ValueError("法案番号またはURLを再確認できません")
    bill_no, url = parsed
    bills = load_json(bills_path, [])
    bill = next((item for item in bills if item.get("no") == bill_no), None)
    if bill is None:
        raise ValueError(f"対象法案が見つかりません: {bill_no}")
    return not any(ref.get("url") == url for ref in bill.get("refs", []))


def set_output(name, value):
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as target:
            target.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    submission = commands.add_parser("approve-submission")
    submission.add_argument("--record", default="rec.json")
    submission.add_argument("--submissions", default="submissions.json")
    removal = commands.add_parser("approve-removal")
    removal.add_argument("--title", default="")
    removal.add_argument("--body", default="")
    removal.add_argument("--bills", default="bills.json")
    removal.add_argument("--suppressions", default="suppressions.json")
    verify = commands.add_parser("verify-removal")
    verify.add_argument("--title", default="")
    verify.add_argument("--body", default="")
    verify.add_argument("--bills", default="bills.json")
    args = parser.parse_args()

    if args.command == "approve-submission":
        detail = approve_submission(args.record, args.submissions)
        result = "published-ready"
    elif args.command == "approve-removal":
        result, detail = approve_removal(
            args.title, args.body, args.bills, args.suppressions
        )
    else:
        if not verify_removal(args.title, args.body, args.bills):
            print("取り下げ対象URLが公開データに残っています")
            return 1
        result, detail = "verified", "公開データからの除外を確認しました"
    set_output("result", result)
    set_output("detail", detail)
    print(detail)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
