# -*- coding: utf-8 -*-
"""前会期からの継続法案（第222回の一覧に載る第221回提出の法案）のテスト。"""
import json

import collect
import enrich


MEISAI = "https://www.sangiin.go.jp/japanese/joho1/kousei/gian/222/meisai/"


def raw_bill(submitted_diet="221回", no="1", continuation="衆継続",
             shu_referred="令和8年10月5日"):
    """参議院の明細ページを parse_bill した形（第222回に継続した衆法1号を模す）。"""
    return {
        "url": MEISAI + "m222090221001.htm", "pdf": None,
        "title": "政治資金規正法の一部を改正する法律案",
        "kind_raw": "法律案（衆法）", "diet": submitted_diet, "no": no,
        "head": {"提出日": "令和8年3月2日", "先議区分": "衆先議", "継続区分": continuation},
        "sections": {
            "衆議院委員会等経過": {"本付託日": shu_referred,
                                  "付託委員会等": "政治改革に関する特別委員会",
                                  "議決日": "", "議決": ""},
            "衆議院本会議経過": {"議決日": "", "議決": ""},
            "参議院委員会等経過": {"本付託日": "", "議決日": "", "議決": ""},
            "参議院本会議経過": {"議決日": "", "議決": ""},
            "その他": {"公布年月日": "", "法律番号": ""},
        },
    }


def test_bill_ref_from_url_reads_submitted_diet_and_number():
    assert collect.bill_ref_from_url(MEISAI + "m222090221001.htm") == ("221", "1")
    assert collect.bill_ref_from_url(MEISAI + "m222080222010.htm") == ("222", "10")
    # 形式外のURL（テスト用など）は末尾3桁を番号として扱う
    assert collect.bill_ref_from_url("https://example.test/007.html") == (None, "7")


def test_make_id_keeps_old_format_and_disambiguates_carried_bills():
    assert collect.make_id("222", "衆法", "1") == "222-衆法-1"
    assert collect.make_id("222", "衆法", "1", "222回") == "222-衆法-1"
    assert collect.make_id("222", "衆法", "1", "221回") == "222-衆法-221-1"


def test_carried_bill_belongs_to_listing_session(monkeypatch):
    calls = []
    monkeypatch.setattr(collect, "kokkai_refs",
                        lambda title, diet, sections: calls.append(diet) or [])
    rec = collect.normalize(raw_bill(), type_hint="衆法", diet="222")
    assert rec["id"] == "222-衆法-221-1"
    assert rec["diet"] == "222回"
    assert rec["submittedDiet"] == "221回"
    assert rec["no"] == "衆法 第1号（第221回提出）"
    assert "継続" in rec["dietLabel"]
    assert calls == ["222"]          # 会議録は掲載会期で検索する
    assert collect.record_diet(rec) == "222"


def test_carried_in_marker_is_not_continuation_status(monkeypatch):
    """「継続区分=衆継続」は引き継がれてきた印。付託済みなら審議中のはず。"""
    monkeypatch.setattr(collect, "kokkai_refs", lambda *a, **k: [])
    rec = collect.normalize(raw_bill(), type_hint="衆法", diet="222",
                            session_closed=False)
    assert rec["status"] == "審議中"
    assert rec["statusDetail"] == "衆・委員会審査中"


def test_carried_bill_uses_listing_session_for_close_check(monkeypatch):
    """提出回次（閉会済みの221回）ではなく掲載会期（開会中の222回）で閉会判定する。"""
    monkeypatch.setattr(collect, "kokkai_refs", lambda *a, **k: [])
    seen = []
    monkeypatch.setattr(collect.sessions, "is_closed_for_status",
                        lambda diet, *a, **k: seen.append(diet) or diet == "221")
    rec = collect.normalize(raw_bill(), type_hint="衆法", diet="222")
    assert seen == ["222"]
    assert rec["status"] == "審議中"


def test_bill_submitted_in_session_keeps_legacy_shape(monkeypatch):
    monkeypatch.setattr(collect, "kokkai_refs", lambda *a, **k: [])
    raw = raw_bill(submitted_diet="222回", continuation="")
    rec = collect.normalize(raw, type_hint="衆法", diet="222", session_closed=False)
    assert rec["id"] == "222-衆法-1"
    assert rec["no"] == "衆法 第1号"
    assert rec["dietLabel"] == "第222回"


def test_new_session_run_does_not_duplicate_carried_bills(tmp_path, monkeypatch):
    """221回のレコードを残したまま222回を2回収集しても id が重複しないこと。"""
    output = tmp_path / "bills.json"
    existing = [{"id": "221-衆法-1", "diet": "221回", "no": "衆法 第1号",
                 "title": "政治資金規正法の一部を改正する法律案",
                 "status": "継続審査", "refs": []}]
    output.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    bills = [{"type": "衆法", "title": "旧", "url": MEISAI + "m222090221001.htm"},
             {"type": "衆法", "title": "新", "url": MEISAI + "m222090222001.htm"}]
    monkeypatch.setattr(collect, "discover_bills", lambda d: bills)
    monkeypatch.setattr(collect, "kokkai_refs", lambda *a, **k: [])
    monkeypatch.setattr(collect, "parse_bill",
                        lambda url: raw_bill(submitted_diet=f"{url[-10:-7]}回"))
    monkeypatch.setattr("sys.argv", ["collect.py", "--diet", "222", "--output", str(output),
                                     "--no-enrich", "--sleep", "0"])
    for _ in range(2):
        assert collect.main() == 0
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert [r["id"] for r in saved] == ["221-衆法-1", "222-衆法-221-1", "222-衆法-1"]
    assert saved[0]["status"] == "継続審査"      # 221回の閉会時点の状態は保持


def test_duplicate_ids_after_merge_abort_the_run(tmp_path, monkeypatch):
    output = tmp_path / "bills.json"
    existing = [{"id": "222-閣法-1", "diet": "221回", "no": "閣法 第1号",
                 "title": "x", "status": "審議中", "refs": []}]
    output.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(collect, "discover_bills", lambda d: [
        {"type": "閣法", "title": "x", "url": "https://example.test/001.html"}])
    monkeypatch.setattr(collect, "parse_bill", lambda url: {})
    monkeypatch.setattr(collect, "normalize", lambda raw, **_: {
        "id": "222-閣法-1", "diet": "222回", "no": "閣法 第1号",
        "title": "x", "status": "審議中", "refs": []})
    monkeypatch.setattr("sys.argv", ["collect.py", "--diet", "222", "--output", str(output),
                                     "--no-enrich", "--sleep", "0"])
    assert collect.main() == 1
    assert json.loads(output.read_text(encoding="utf-8")) == existing


def test_diet_defaults_to_latest_session(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(collect.sessions, "latest_diet", lambda: 222)
    monkeypatch.setattr(collect, "discover_bills",
                        lambda d: seen.append(d) or (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setattr("sys.argv", ["collect.py", "--output", str(tmp_path / "b.json"),
                                     "--no-enrich"])
    assert collect.main() == 1
    assert seen == ["222"]


def test_enrich_matches_shugiin_rows_by_submitted_diet(monkeypatch):
    smap = {("衆法", "221", "1"): {"houan": "https://shugiin.example/221-1"},
            ("衆法", "222", "1"): {"houan": "https://shugiin.example/222-1"}}
    monkeypatch.setattr(enrich, "build_ministry_map", lambda *a, **k: {})
    monkeypatch.setattr(enrich, "build_shugiin_map", lambda diet: smap)
    monkeypatch.setattr(enrich, "fetch_shugiin_reason", lambda url: None)
    carried = {"type": "衆法", "no": "衆法 第1号（第221回提出）",
               "diet": "222回", "submittedDiet": "221回", "refs": []}
    fresh = {"type": "衆法", "no": "衆法 第1号",
             "diet": "222回", "submittedDiet": "222回", "refs": []}
    enrich.enrich([carried, fresh], "222", sleep=0)
    assert carried["refs"][0]["url"].endswith("221-1")
    assert fresh["refs"][0]["url"].endswith("222-1")


def test_enrich_looks_up_carried_cabinet_bill_in_its_own_session(monkeypatch):
    maps = {"222": {"1": {"ministry": "新法の省"}},
            "221": {"1": {"ministry": "継続法の省"}}}
    monkeypatch.setattr(enrich, "build_ministry_map",
                        lambda diet, clb_id=None: maps[enrich.sessions.normalize_diet(diet)])
    monkeypatch.setattr(enrich, "build_shugiin_map", lambda diet: {})
    monkeypatch.setattr(enrich, "fetch_reason", lambda url: {})
    carried = {"type": "閣法", "no": "閣法 第1号（第221回提出）",
               "diet": "222回", "submittedDiet": "221回", "refs": []}
    fresh = {"type": "閣法", "no": "閣法 第1号", "diet": "222回", "refs": []}
    enrich.enrich([carried, fresh], "222", sleep=0)
    assert carried["ministry"] == "継続法の省"
    assert fresh["ministry"] == "新法の省"
