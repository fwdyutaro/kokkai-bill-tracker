import json

import apply_suppressions as apply


def test_new_suppression_targets_only_bill_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    url = "https://example.test/ref"
    bills = [
        {"id": "221-閣法-1", "no": "閣法 第1号", "refs": [{"url": url}]},
        {"id": "222-閣法-1", "no": "閣法 第1号", "refs": [{"url": url}]},
    ]
    (tmp_path / "bills.json").write_text(json.dumps(bills, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "suppressions.json").write_text(json.dumps([{"bill_id": "221-閣法-1", "bill_no": "閣法 第1号", "url": url, "status": "approved"}], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "data_collected.js").write_text("", encoding="utf-8")
    apply.main()
    saved = json.loads((tmp_path / "bills.json").read_text(encoding="utf-8"))
    assert saved[0]["refs"] == []
    assert len(saved[1]["refs"]) == 1


def test_targeted_and_legacy_suppressions_both_apply_to_same_bill(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    targeted = "https://example.test/targeted"
    legacy = "https://example.test/legacy"
    bills = [{"id": "221-閣法-1", "no": "閣法 第1号", "refs": [{"url": targeted}, {"url": legacy}]}]
    suppressions = [
        {"bill_id": "221-閣法-1", "bill_no": "閣法 第1号", "url": targeted, "status": "approved"},
        {"bill_no": "閣法 第1号", "url": legacy, "status": "approved"},
    ]
    (tmp_path / "bills.json").write_text(json.dumps(bills, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "suppressions.json").write_text(json.dumps(suppressions, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "data_collected.js").write_text("", encoding="utf-8")
    apply.main()
    saved = json.loads((tmp_path / "bills.json").read_text(encoding="utf-8"))
    assert saved[0]["refs"] == []
