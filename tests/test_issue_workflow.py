import json

import issue_workflow as workflow


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_submission_is_added_once(tmp_path):
    record = tmp_path / "rec.json"
    submissions = tmp_path / "submissions.json"
    dump(record, {"bill_no": "閣法 第1号", "url": "https://example.test/ref"})
    dump(submissions, [])

    assert workflow.approve_submission(record, submissions) == "added"
    assert workflow.approve_submission(record, submissions) == "already-listed"
    saved = json.loads(submissions.read_text(encoding="utf-8"))
    assert len(saved) == 1
    assert saved[0]["status"] == "approved"


def test_submission_duplicate_is_scoped_to_bill_identity(tmp_path):
    record = tmp_path / "rec.json"; submissions = tmp_path / "submissions.json"
    dump(record, {"bill_id": "222-閣法-1", "bill_no": "閣法 第1号", "url": "https://example.test/ref"})
    dump(submissions, [{"bill_id": "221-閣法-1", "bill_no": "閣法 第1号", "url": "https://example.test/ref", "status": "approved"}])
    assert workflow.approve_submission(record, submissions) == "added"


def test_submission_unknown_bill_id_is_manual_review(tmp_path):
    record = tmp_path / "rec.json"; submissions = tmp_path / "submissions.json"; bills = tmp_path / "bills.json"
    dump(record, {"bill_id": "999-閣法-1", "bill_no": "閣法 第1号", "url": "https://example.test/ref"})
    dump(submissions, []); dump(bills, [{"id": "221-閣法-1", "no": "閣法 第1号"}])
    assert workflow.approve_submission(record, submissions, bills) == "manual_review"
    assert json.loads(submissions.read_text(encoding="utf-8")) == []


def test_submission_ambiguous_legacy_bill_no_is_manual_review(tmp_path):
    record = tmp_path / "rec.json"; submissions = tmp_path / "submissions.json"; bills = tmp_path / "bills.json"
    dump(record, {"bill_no": "閣法 第1号", "url": "https://example.test/ref"})
    dump(submissions, []); dump(bills, [{"id": "221-閣法-1", "no": "閣法 第1号"}, {"id": "222-閣法-1", "no": "閣法 第1号"}])
    assert workflow.approve_submission(record, submissions, bills) == "manual_review"


def test_removal_with_unknown_bill_id_is_manual_review(tmp_path):
    bills = tmp_path / "bills.json"; suppressions = tmp_path / "suppressions.json"
    dump(bills, [{"id": "221-閣法-1", "no": "閣法 第1号", "refs": [{"url": "https://example.test/ref"}]}]); dump(suppressions, [])
    result, _ = workflow.approve_removal("bill_id: 999-閣法-1 閣法 第1号", "https://example.test/ref", bills, suppressions)
    assert result == "manual_review"


def test_removal_requires_existing_bill_and_reference(tmp_path):
    bills = tmp_path / "bills.json"
    suppressions = tmp_path / "suppressions.json"
    dump(bills, [{"no": "閣法 第1号", "refs": []}])
    dump(suppressions, [])

    result, _ = workflow.approve_removal(
        "閣法 第1号", "https://example.test/missing", bills, suppressions
    )
    assert result == "manual_review"
    assert json.loads(suppressions.read_text(encoding="utf-8")) == []


def test_removal_is_added_once(tmp_path):
    url = "https://example.test/ref"
    bills = tmp_path / "bills.json"
    suppressions = tmp_path / "suppressions.json"
    dump(bills, [{"no": "参法 第2号", "refs": [{"url": url}]}])
    dump(suppressions, [])

    assert workflow.approve_removal("参法 第2号", url, bills, suppressions)[0] == "published-ready"
    assert workflow.approve_removal("参法 第2号", url, bills, suppressions)[0] == "published-ready"
    assert len(json.loads(suppressions.read_text(encoding="utf-8"))) == 1


def test_removal_verification_requires_url_to_be_absent(tmp_path):
    url = "https://example.test/ref"
    bills = tmp_path / "bills.json"
    dump(bills, [{"no": "閣法 第1号", "refs": [{"url": url}]}])
    assert not workflow.verify_removal("閣法 第1号", url, bills)
    dump(bills, [{"no": "閣法 第1号", "refs": []}])
    assert workflow.verify_removal("閣法 第1号", url, bills)
