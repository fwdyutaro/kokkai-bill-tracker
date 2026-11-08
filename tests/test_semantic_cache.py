# -*- coding: utf-8 -*-
"""意味類似候補キャッシュ（semantic_cache.json）。

週次ローカル処理（e5）が付けた意味類似リンクは、CI で collect.py が最新会期の
レコードを作り直すと消える。週次処理で保存したキャッシュを CI（--no-semantic）で
再適用し、その際も取り下げ・LLMゲート・ID誤適用の防止を通すことを担保する。
"""
import json
import sqlite3
import sys

import pytest

import llm_gate
import match_refs


BILL_ID = "222-閣法-1"
BILL = {"id": BILL_ID, "no": "閣法 第1号", "title": "郵便法の一部を改正する法律案",
        "ministry": "総務省", "submittedOn": "2026-10-09", "summary": "", "refs": []}
PRIMARY = {"tier": 1, "cat": "一次資料", "pub": "参議院", "title": "議案情報",
           "url": "https://www.sangiin.go.jp/example"}

URL_OK = "https://dl.ndl.go.jp/pid/1"
URL_REJECTED = "https://dl.ndl.go.jp/pid/2"
URL_UNJUDGED = "https://dl.ndl.go.jp/pid/3"
URL_SUPPRESSED = "https://dl.ndl.go.jp/pid/4"
DOCS = [
    (URL_OK, "物流の将来像に関する調査"),
    (URL_REJECTED, "海外の宇宙開発動向"),
    (URL_UNJUDGED, "地方鉄道の現状"),
    (URL_SUPPRESSED, "通信市場の競争評価"),
]


def gate_key(bill, url):
    return llm_gate.Gate.__new__(llm_gate.Gate)._key(bill, {"url": url})


def sem_ref(url, title, cos=0.90, llm="／LLM確認済"):
    return {"tier": 2, "cat": "立法補佐機関", "pub": "国立国会図書館", "title": title,
            "url": url, "conf": 70,
            "confNote": f"自動紐付け: 意味類似・要確認候補（multilingual-e5 {cos:.2f}）{llm}"}


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """CI 相当: LLMバックエンド無し（キャッシュのみ）、作業ディレクトリは一時領域。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm_gate, "_backend", lambda: None)
    con = sqlite3.connect(tmp_path / "refs.db")
    con.execute("CREATE TABLE documents (tier, category, publisher, title, url, "
                "published_at, ministry, abstract)")
    con.executemany("INSERT INTO documents VALUES (2,'立法補佐機関','国立国会図書館',?,?,NULL,NULL,NULL)",
                    [(t, u) for u, t in DOCS])
    con.commit()
    con.close()
    write(tmp_path, "suppressions.json", [
        {"bill_id": BILL_ID, "bill_no": "閣法 第1号", "url": URL_SUPPRESSED, "status": "approved"}])
    write(tmp_path, "gate_cache.json", {
        gate_key(BILL, URL_OK): {"related": True, "reason": "関連する"},
        gate_key(BILL, URL_REJECTED): {"related": False, "reason": "無関係"},
        gate_key(BILL, URL_SUPPRESSED): {"related": True, "reason": "関連する"},
    })
    return tmp_path


def write(d, name, obj):
    (d / name).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def read(d, name):
    return json.loads((d / name).read_text(encoding="utf-8"))


def run_match_refs(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["match_refs.py", *args])
    match_refs.main()


def fresh_bill(**over):
    """collect.py が作り直した直後のレコード（refs は一次資料のみ）。"""
    return {**BILL, "refs": [dict(PRIMARY)], **over}


def cache_entry(bill, urls):
    titles = dict(DOCS)
    return {**match_refs.bill_fingerprint(bill), "candidates": [
        {"url": u, "score": 0.7, "why": "意味類似・要確認候補（multilingual-e5 0.90）",
         "tier": 2, "cat": "立法補佐機関", "pub": "国立国会図書館", "title": titles[u]}
        for u in urls]}


def sem_urls(bill):
    return {r["url"] for r in bill["refs"] if "意味類似" in (r.get("confNote") or "")}


# ---- 単体 ---------------------------------------------------------------------

def test_build_cache_keeps_only_semantic_refs_and_strips_llm_note(workdir):
    bill = fresh_bill(refs=[dict(PRIMARY),
                            {**sem_ref(URL_OK, "物流"), "confNote": "自動紐付け: 法案名が一致"},
                            sem_ref(URL_UNJUDGED, "地方鉄道の現状", llm="／LLM確認: 関連する")])
    entries = match_refs.build_semantic_cache([bill])

    assert list(entries) == [BILL_ID]
    entry = entries[BILL_ID]
    assert entry["no"] == "閣法第1号" and entry["title"] == "郵便法の一部を改正する法律案"
    assert [c["url"] for c in entry["candidates"]] == [URL_UNJUDGED]
    assert entry["candidates"][0]["why"] == "意味類似・要確認候補（multilingual-e5 0.90）"
    assert entry["candidates"][0]["score"] == 0.7


def test_build_cache_drops_suppressed_and_gate_rejected(workdir):
    bill = fresh_bill(refs=[sem_ref(u, t) for u, t in DOCS])
    entries = match_refs.build_semantic_cache(
        [bill], match_refs.load_suppression_check(), llm_gate.Gate())

    urls = {c["url"] for c in entries[BILL_ID]["candidates"]}
    assert urls == {URL_OK, URL_UNJUDGED}


def test_build_cache_skips_records_without_semantic_refs(workdir):
    assert match_refs.build_semantic_cache([fresh_bill()]) == {}


def test_replay_skips_taken_and_suppressed_urls(workdir):
    entry = cache_entry(BILL, [URL_OK, URL_UNJUDGED, URL_SUPPRESSED])
    out = match_refs.replay_candidates(BILL, entry, {URL_UNJUDGED},
                                       match_refs.load_suppression_check())
    assert [d["url"] for _, _, d in out] == [URL_OK]
    sc, why, doc = out[0]
    assert sc == 0.7 and "意味類似" in why
    assert doc["category"] == "立法補佐機関" and doc["publisher"] == "国立国会図書館"


@pytest.mark.parametrize("change", [{"title": "別の法律案"}, {"no": "閣法 第2号"}])
def test_replay_refuses_entry_whose_bill_identity_changed(change):
    entry = cache_entry(BILL, [URL_OK])
    assert match_refs.replay_candidates({**BILL, **change}, entry, set()) == []


def test_load_cache_ignores_unknown_version_and_broken_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert match_refs.load_semantic_cache() == {}
    (tmp_path / "semantic_cache.json").write_text("{broken", encoding="utf-8")
    assert match_refs.load_semantic_cache() == {}
    write(tmp_path, "semantic_cache.json", {"version": 999, "bills": {BILL_ID: {}}})
    assert match_refs.load_semantic_cache() == {}


# ---- CI 相当（--no-semantic）での再適用 --------------------------------------------

def test_ci_replays_only_gate_approved_unsuppressed_candidates(workdir, monkeypatch):
    write(workdir, "bills.json", [fresh_bill()])
    match_refs.save_semantic_cache({BILL_ID: cache_entry(BILL, [u for u, _ in DOCS])})

    run_match_refs(monkeypatch, "--no-semantic")

    bill = read(workdir, "bills.json")[0]
    # 承認済みだけが戻る。無関係判定・未判定・取り下げ済みは復活しない。
    assert sem_urls(bill) == {URL_OK}
    ref = next(r for r in bill["refs"] if r["url"] == URL_OK)
    assert ref["confNote"] == ("自動紐付け: 意味類似・要確認候補（multilingual-e5 0.90）"
                               "／LLM確認: 関連する")
    assert ref["conf"] == 70 and ref["title"] == "物流の将来像に関する調査"
    # 一次資料は保持され、キャッシュは CI では書き換えない
    assert bill["refs"][0] == PRIMARY
    assert read(workdir, "semantic_cache.json")["bills"][BILL_ID]["candidates"][0]["url"] == URL_OK


def test_ci_does_not_duplicate_links_already_on_record(workdir, monkeypatch):
    write(workdir, "bills.json", [fresh_bill(refs=[dict(PRIMARY), sem_ref(URL_OK, "物流")])])
    match_refs.save_semantic_cache({BILL_ID: cache_entry(BILL, [URL_OK])})

    run_match_refs(monkeypatch, "--no-semantic")

    urls = [r["url"] for r in read(workdir, "bills.json")[0]["refs"]]
    assert urls.count(URL_OK) == 1


def test_ci_does_not_apply_cache_to_reused_id(workdir, monkeypatch):
    # 同じIDが別法案に付け替わった（件名が違う）場合は適用しない
    other = fresh_bill(title="所得税法の一部を改正する法律案")
    write(workdir, "bills.json", [other])
    match_refs.save_semantic_cache({BILL_ID: cache_entry(BILL, [URL_OK])})

    run_match_refs(monkeypatch, "--no-semantic")

    assert sem_urls(read(workdir, "bills.json")[0]) == set()


def test_ci_does_not_apply_cache_to_other_session_with_same_number(workdir, monkeypatch):
    # 前会期の同番号法案（ID違い）には適用しない
    write(workdir, "bills.json", [fresh_bill(id="221-閣法-1")])
    match_refs.save_semantic_cache({BILL_ID: cache_entry(BILL, [URL_OK])})

    run_match_refs(monkeypatch, "--no-semantic")

    assert sem_urls(read(workdir, "bills.json")[0]) == set()


def test_replay_requires_a_gate(workdir, monkeypatch):
    write(workdir, "bills.json", [fresh_bill()])
    match_refs.save_semantic_cache({BILL_ID: cache_entry(BILL, [URL_OK])})

    run_match_refs(monkeypatch, "--no-semantic", "--no-llm-gate")

    assert sem_urls(read(workdir, "bills.json")[0]) == set()


def test_no_semantic_cache_flag_disables_replay(workdir, monkeypatch):
    write(workdir, "bills.json", [fresh_bill()])
    match_refs.save_semantic_cache({BILL_ID: cache_entry(BILL, [URL_OK])})

    run_match_refs(monkeypatch, "--no-semantic", "--no-semantic-cache")

    assert sem_urls(read(workdir, "bills.json")[0]) == set()


# ---- 週次（e5）→ CI の往復 ---------------------------------------------------------

def fake_semantic(backend, top_url):
    def build(bills, docs):
        sims = [[0.95 if d["url"] == top_url else 0.5 for d in docs] for _ in bills]
        return sims, 0.85, backend
    return build


def test_weekly_e5_run_saves_cache_and_ci_restores_it(workdir, monkeypatch):
    write(workdir, "bills.json", [fresh_bill()])
    monkeypatch.setattr(match_refs, "build_semantic", fake_semantic("multilingual-e5", URL_OK))

    run_match_refs(monkeypatch)                      # 週次ローカル処理（e5）
    weekly = read(workdir, "bills.json")[0]
    assert sem_urls(weekly) == {URL_OK}
    cache = read(workdir, "semantic_cache.json")
    assert cache["version"] == match_refs.SEMANTIC_CACHE_VERSION
    assert [c["url"] for c in cache["bills"][BILL_ID]["candidates"]] == [URL_OK]

    write(workdir, "bills.json", [fresh_bill()])     # 翌晩 collect.py が作り直す
    run_match_refs(monkeypatch, "--no-semantic")     # CI

    restored = read(workdir, "bills.json")[0]
    assert restored["refs"] == weekly["refs"]


def test_weekly_rerun_keeps_links_already_on_records(workdir, monkeypatch):
    # 2回目の週次実行では既存リンクは新規付与されないが、キャッシュからは消さない
    write(workdir, "bills.json", [fresh_bill(refs=[dict(PRIMARY), sem_ref(URL_OK, "物流")])])
    monkeypatch.setattr(match_refs, "build_semantic", fake_semantic("multilingual-e5", URL_OK))

    run_match_refs(monkeypatch)

    cache = read(workdir, "semantic_cache.json")
    assert [c["url"] for c in cache["bills"][BILL_ID]["candidates"]] == [URL_OK]


def test_weekly_run_prunes_entries_for_vanished_ids(workdir, monkeypatch):
    write(workdir, "bills.json", [fresh_bill()])
    match_refs.save_semantic_cache({"222-衆法-1": cache_entry(BILL, [URL_OK])})
    monkeypatch.setattr(match_refs, "build_semantic", fake_semantic("multilingual-e5", URL_OK))

    run_match_refs(monkeypatch)

    assert list(read(workdir, "semantic_cache.json")["bills"]) == [BILL_ID]


def test_non_authoritative_backend_replays_and_does_not_overwrite_cache(workdir, monkeypatch):
    write(workdir, "bills.json", [fresh_bill()])
    match_refs.save_semantic_cache({BILL_ID: cache_entry(BILL, [URL_OK])})
    before = (workdir / "semantic_cache.json").read_text(encoding="utf-8")
    monkeypatch.setattr(match_refs, "build_semantic", fake_semantic("tfidf(char)", URL_UNJUDGED))

    run_match_refs(monkeypatch)

    assert URL_OK in sem_urls(read(workdir, "bills.json")[0])
    assert (workdir / "semantic_cache.json").read_text(encoding="utf-8") == before


def test_dry_run_does_not_write_cache(workdir, monkeypatch):
    write(workdir, "bills.json", [fresh_bill()])
    monkeypatch.setattr(match_refs, "build_semantic", fake_semantic("multilingual-e5", URL_OK))

    run_match_refs(monkeypatch, "--dry")

    assert not (workdir / "semantic_cache.json").exists()


# ---- LLMゲートの厳格モード ------------------------------------------------------

def test_gate_require_verdict_drops_unjudged_and_rejected(workdir):
    gate = llm_gate.Gate()
    cands = [(0.7, "意味類似・要確認候補（multilingual-e5 0.90）", {"url": u, "title": t})
             for u, t in DOCS[:3]]
    kept = gate.filter(BILL, cands, require_verdict=True)
    assert [d["url"] for _, _, d in kept] == [URL_OK]
    assert gate.dirty is False
