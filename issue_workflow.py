# -*- coding: utf-8 -*-
"""GitHub Issue 承認ワークフローの、ローカルで検証可能な状態判定。"""

import argparse
import json
import os
import re
from pathlib import Path


BILL_NO_RE = re.compile(r"(閣法|衆法|参法)\s*第?\s*(\d+)\s*号")
BILL_ID_RE = re.compile(r"\b(\d+-(?:閣法|衆法|参法)-?\d+)\b")
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


def parse_removal_details(title, body):
    text = f"{title}\n{body or ''}"
    bill_match = BILL_NO_RE.search(text)
    id_match = BILL_ID_RE.search(text)
    url_match = URL_RE.search(body or "")
    if not (bill_match or id_match) or not url_match:
        return None
    bill_no = (bill_match.group(1), bill_match.group(2)) if bill_match else None
    return (id_match.group(1) if id_match else None, bill_no, url_match.group(0))


def parse_removal(title, body):
    """旧API。bill_idを扱う内部処理は parse_removal_details を使う。"""
    details = parse_removal_details(title, body)
    if not details or not details[1]:
        return None
    kind, number = details[1]
    return f"{kind} 第{int(number)}号", details[2]


def _resolve_record_bill(record, bills):
    bill_id = record.get("bill_id")
    bill_no = record.get("bill_no")
    if bill_id:
        bill = next((item for item in bills if item.get("id") == bill_id), None)
        if bill is None:
            return None
        if bill_no and _normalise_bill_no(bill.get("no")) != _normalise_bill_no(bill_no):
            return None
        return bill
    wanted = _normalise_bill_no(bill_no)
    if wanted is None:
        return None
    candidates = [item for item in bills if _normalise_bill_no(item.get("no")) == wanted]
    return candidates[0] if len(candidates) == 1 else None


def approve_submission(record_path, submissions_path, bills_path=None):
    record = load_json(record_path, None)
    if not isinstance(record, dict) or not (record.get("bill_no") or record.get("bill_id")) or not record.get("url"):
        raise ValueError("掲載レコードが生成されていないか、必須項目がありません")
    submissions = load_json(submissions_path, [])
    if not isinstance(submissions, list):
        raise ValueError("submissions.json は配列である必要があります")
    if bills_path is not None:
        bills = load_json(bills_path, None)
        if not isinstance(bills, list) or _resolve_record_bill(record, bills) is None:
            return "manual_review"
    duplicate = any(
        item.get("url") == record["url"] and
        ((record.get("bill_id") and item.get("bill_id") == record.get("bill_id")) or
         (not record.get("bill_id") and not item.get("bill_id") and item.get("bill_no") == record.get("bill_no")))
        for item in submissions
    )
    if not duplicate:
        record["status"] = "approved"
        submissions.append(record)
        write_json(submissions_path, submissions)
    return "already-listed" if duplicate else "added"


def approve_removal(title, body, bills_path, suppressions_path):
    parsed = parse_removal_details(title, body)
    if not parsed:
        return "manual_review", "法案番号またはURLを解釈できませんでした"
    bill_id, bill_no_tuple, url = parsed
    bills = load_json(bills_path, [])
    bill = next((item for item in bills if item.get("id") == bill_id), None) if bill_id else None
    if bill_id and bill is None:
        return "manual_review", f"対象法案が見つかりません: {bill_id}"
    if bill is None and bill_no_tuple:
        wanted = (bill_no_tuple[0], int(bill_no_tuple[1]))
        candidates = [item for item in bills if _normalise_bill_no(item.get("no")) == wanted]
        if len(candidates) == 1:
            bill = candidates[0]
        elif len(candidates) > 1:
            return "manual_review", "議案番号が複数の会期に存在します"
    if bill is None:
        return "manual_review", f"対象法案が見つかりません: {bill_id or bill_no_tuple}"
    if bill_id and bill_no_tuple and _normalise_bill_no(bill.get("no")) != (bill_no_tuple[0], int(bill_no_tuple[1])):
        return "manual_review", "bill_id と議案番号が一致しません"

    suppressions = load_json(suppressions_path, [])
    if not isinstance(suppressions, list):
        raise ValueError("suppressions.json は配列である必要があります")
    duplicate = any(
        item.get("url") == url and (item.get("bill_id") == bill_id if bill_id else item.get("bill_no") == bill.get("no"))
        for item in suppressions
    )
    exists = any(ref.get("url") == url for ref in bill.get("refs", []))
    if not exists and not duplicate:
        return "manual_review", "指定URLは対象法案の参考リンクに存在しません"
    if not duplicate:
        entry = {"bill_id": bill.get("id"), "bill_no": bill.get("no"), "url": url, "status": "approved"}
        suppressions.append(entry)
        write_json(suppressions_path, suppressions)
    detail = "既に取り下げ済みです" if duplicate else "取り下げ対象を確認しました"
    return "published-ready", detail


def verify_removal(title, body, bills_path):
    """取り下げ適用後、対象URLが公開データから消えたことを確認する。"""
    parsed = parse_removal_details(title, body)
    if not parsed:
        raise ValueError("法案番号またはURLを再確認できません")
    bill_id, bill_no_tuple, url = parsed
    bills = load_json(bills_path, [])
    bill = next((item for item in bills if item.get("id") == bill_id), None) if bill_id else None
    if bill_id and bill is None:
        raise ValueError(f"対象法案が見つかりません: {bill_id}")
    if bill is None and bill_no_tuple:
        wanted = (bill_no_tuple[0], int(bill_no_tuple[1]))
        candidates = [item for item in bills if _normalise_bill_no(item.get("no")) == wanted]
        bill = candidates[0] if len(candidates) == 1 else None
    if bill is None:
        raise ValueError(f"対象法案が見つかりません: {bill_id or bill_no_tuple}")
    if bill_id and bill_no_tuple and _normalise_bill_no(bill.get("no")) != (bill_no_tuple[0], int(bill_no_tuple[1])):
        raise ValueError("bill_id と議案番号が一致しません")
    return not any(ref.get("url") == url for ref in bill.get("refs", []))


def _normalise_bill_no(value):
    match = BILL_NO_RE.search(str(value or ""))
    return (match.group(1), int(match.group(2))) if match else None


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
    submission.add_argument("--bills", default="bills.json")
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
        detail = approve_submission(args.record, args.submissions, args.bills)
        result = "manual_review" if detail == "manual_review" else "published-ready"
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
