# -*- coding: utf-8 -*-
"""会期ごとのマージ（bills.json が複数会期を保持すること）のテスト。"""
import json

import collect


def record(number, diet="221回", kind="閣法", refs=None):
    return {"id": f"{diet.replace('回','')}-{kind}-{number}", "diet": diet,
            "no": f"{kind} 第{number}号", "title": f"法案{number}",
            "status": "審議中", "refs": refs if refs is not None else []}


def test_record_diet_reads_field_and_falls_back_to_id():
    assert collect.record_diet(record(1)) == "221"
    assert collect.record_diet({"id": "222-閣法-3"}) == "222"
    assert collect.record_diet({"id": "x"}) is None


def test_merge_replaces_only_the_requested_diet():
    existing = [record(1), record(2), record(1, diet="222回")]
    fresh = [record(1, diet="222回"), record(2, diet="222回")]
    merged = collect.merge_by_diet(existing, fresh, "222")
    assert [r["id"] for r in merged] == ["221-閣法-1", "221-閣法-2",
                                          "222-閣法-1", "222-閣法-2"]


def test_merge_keeps_position_of_the_replaced_diet():
    existing = [record(1, diet="222回"), record(1), record(2)]
    merged = collect.merge_by_diet(existing, [record(9, diet="222回")], "222")
    assert [r["id"] for r in merged] == ["222-閣法-9", "221-閣法-1", "221-閣法-2"]


def test_merge_appends_a_brand_new_diet():
    existing = [record(1), record(2)]
    merged = collect.merge_by_diet(existing, [record(1, diet="222回")], "222")
    assert [r["id"] for r in merged] == ["221-閣法-1", "221-閣法-2", "222-閣法-1"]


def test_select_diet_and_meeting_ref_count_are_scoped():
    kaigi = [{"cat": "会議録", "url": "u"}]
    existing = [record(1, refs=kaigi), record(1, diet="222回", refs=kaigi * 5)]
    assert len(collect.select_diet(existing, "221回")) == 1
    assert collect.count_meeting_refs(collect.select_diet(existing, "221")) == 1
    assert collect.count_meeting_refs(collect.select_diet(existing, "222")) == 5


def _run_collect(tmp_path, monkeypatch, diet, numbers, existing):
    output = tmp_path / "bills.json"
    output.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    bills = [{"type": "閣法", "title": f"法案{i}",
              "url": f"https://example.test/{i:03}.html"} for i in numbers]
    monkeypatch.setattr(collect, "discover_bills", lambda d: bills)
    monkeypatch.setattr(collect, "parse_bill",
                        lambda url: {"n": int(url.rsplit("/", 1)[1].split(".", 1)[0])})
    monkeypatch.setattr(collect, "normalize",
                        lambda raw, type_hint=None: record(raw["n"], diet=f"{diet}回"))
    monkeypatch.setattr("sys.argv", ["collect.py", "--diet", diet, "--output", str(output),
                                     "--no-enrich", "--sleep", "0"])
    code = collect.main()
    return code, json.loads(output.read_text(encoding="utf-8"))


def test_new_diet_run_keeps_previous_diet_records(tmp_path, monkeypatch):
    """--diet 222 の実行で 221 のレコードが消えないこと。"""
    existing = [record(i) for i in range(1, 101)]
    code, saved = _run_collect(tmp_path, monkeypatch, "222", range(1, 11), existing)
    assert code == 0
    assert len(saved) == 110
    assert sum(1 for r in saved if collect.record_diet(r) == "221") == 100
    assert sum(1 for r in saved if collect.record_diet(r) == "222") == 10


def test_decrease_guard_compares_within_the_same_diet(tmp_path, monkeypatch):
    """他会期の件数で水増しされず、同一会期の10%以上の減少を検知すること。"""
    existing = ([record(i) for i in range(1, 101)]
                + [record(i, diet="222回") for i in range(1, 101)])
    code, _ = _run_collect(tmp_path, monkeypatch, "222", range(1, 51), existing)
    assert code == 1


def test_new_diet_with_few_bills_does_not_trip_the_guard(tmp_path, monkeypatch):
    """新会期の初回収集（既存0件）はガードに引っかからないこと。"""
    existing = [record(i) for i in range(1, 101)]
    code, saved = _run_collect(tmp_path, monkeypatch, "222", range(1, 4), existing)
    assert code == 0
    assert len(saved) == 103


def test_unregistered_diet_warns_but_continues(tmp_path, monkeypatch, capsys):
    code, saved = _run_collect(tmp_path, monkeypatch, "999", range(1, 4), [])
    assert code == 0 and len(saved) == 3
    assert "sessions.yaml に第999回国会の登録がありません" in capsys.readouterr().err
