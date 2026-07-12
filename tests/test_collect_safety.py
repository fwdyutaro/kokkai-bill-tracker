import json
from pathlib import Path

import pytest

import collect


def record(number):
    return {"id": f"221-閣法-{number}", "no": f"閣法 第{number}号",
            "title": f"法案{number}", "status": "審議中", "refs": []}


def test_validation_rejects_ten_percent_parse_failures():
    errors = collect.validate_records([record(i) for i in range(1, 91)], 100, 100, 90)
    assert any("成功率" in error for error in errors)


def test_validation_accepts_two_percent_failures_when_fallbacks_are_kept():
    records = [record(i) for i in range(1, 101)]
    assert collect.validate_records(records, 100, 100, 98, existing_count=100) == []


def test_validation_rejects_large_meeting_reference_drop():
    records = [record(1)]
    errors = collect.validate_records(
        records, 1, 1, 1, existing_count=1, existing_meeting_ref_count=20
    )
    assert any("会議録リンク" in error for error in errors)


def test_validation_allows_meeting_reference_drop_when_explicit():
    records = [record(1)]
    assert collect.validate_records(
        records, 1, 1, 1, existing_count=1, existing_meeting_ref_count=20,
        allow_large_decrease=True,
    ) == []


def test_save_records_replaces_json_and_public_js(tmp_path):
    output = tmp_path / "bills.json"
    collect.save_records([record(1)], output, write_public_js=True)
    assert json.loads(output.read_text(encoding="utf-8"))[0]["id"] == "221-閣法-1"
    assert (tmp_path / "data_collected.js").read_text(encoding="utf-8").startswith("window.BILLS = [")
    assert not list(tmp_path.glob(".*.tmp"))


def test_save_records_rolls_back_json_when_js_replace_fails(tmp_path, monkeypatch):
    output, js = tmp_path / "bills.json", tmp_path / "data_collected.js"
    output.write_text(json.dumps([record(1)], ensure_ascii=False), encoding="utf-8")
    js.write_text("window.BILLS = [];\n", encoding="utf-8")
    original_replace = collect.os.replace

    def fail_js(source, destination):
        if Path(destination) == js:
            raise OSError("simulated")
        return original_replace(source, destination)

    monkeypatch.setattr(collect.os, "replace", fail_js)
    with pytest.raises(OSError):
        collect.save_records([record(2)], output, write_public_js=True)
    assert json.loads(output.read_text(encoding="utf-8"))[0]["id"] == "221-閣法-1"
    assert js.read_text(encoding="utf-8") == "window.BILLS = [];\n"


def test_partial_run_requires_explicit_nonproduction_output(monkeypatch):
    monkeypatch.setattr("sys.argv", ["collect.py", "--limit", "3"])
    with pytest.raises(SystemExit) as exc:
        collect.main()
    assert exc.value.code == 2


def test_partial_run_writes_only_requested_json(tmp_path, monkeypatch):
    output = tmp_path / "sample.json"
    bills = [{"type": "閣法", "title": f"法案{i}",
              "url": f"https://example.test/{i:03}.html"} for i in range(1, 4)]
    monkeypatch.setattr(collect, "discover_bills", lambda diet: bills)
    monkeypatch.setattr(collect, "parse_bill", lambda url: {"url": url})
    counter = iter(range(1, 4))
    monkeypatch.setattr(collect, "normalize", lambda raw, type_hint=None: record(next(counter)))
    monkeypatch.setattr("sys.argv", ["collect.py", "--limit", "3", "--output", str(output),
                                       "--allow-partial-output", "--no-enrich", "--sleep", "0"])
    assert collect.main() == 0
    assert len(json.loads(output.read_text(encoding="utf-8"))) == 3
    assert not (tmp_path / "data_collected.js").exists()


def test_full_run_preserves_failed_existing_record(tmp_path, monkeypatch):
    output = tmp_path / "bills.json"
    output.write_text(json.dumps([record(i) for i in range(1, 101)], ensure_ascii=False), encoding="utf-8")
    bills = [{"type": "閣法", "title": f"法案{i}",
              "url": f"https://example.test/{i:03}.html"} for i in range(1, 101)]
    monkeypatch.setattr(collect, "discover_bills", lambda diet: bills)

    def parse(url):
        number = int(url.rsplit("/", 1)[1].split(".", 1)[0])
        if number in (10, 20):
            raise RuntimeError("fetch failed")
        return {"number": number}

    monkeypatch.setattr(collect, "parse_bill", parse)
    monkeypatch.setattr(collect, "normalize", lambda raw, type_hint=None: record(raw["number"]))
    monkeypatch.setattr("sys.argv", ["collect.py", "--output", str(output),
                                       "--no-enrich", "--sleep", "0"])
    assert collect.main() == 0
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert len(saved) == 100
    assert {item["id"] for item in saved} == {f"221-閣法-{i}" for i in range(1, 101)}
