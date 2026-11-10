# -*- coding: utf-8 -*-
"""LLMゲートのキャッシュキーと、判定失敗時の扱い。

- 議案番号は会期をまたいで重複する（第221回と第222回の「閣法 第1号」）ため、
  判定は法案の id ごとに持つ。別法案の判定を流用しない。
- 一時的な障害・形式外の応答を「関連あり」としてキャッシュしない
  （再判定されないまま公開され続けるため）。
"""
import pytest

import llm_gate


BILL_221 = {"id": "221-閣法-1", "no": "閣法 第1号", "title": "A法の一部を改正する法律案"}
BILL_222 = {"id": "222-閣法-1", "no": "閣法 第1号", "title": "B法の一部を改正する法律案"}
BILL_UNIQUE = {"id": "221-閣法-54", "no": "閣法 第54号", "title": "C法の一部を改正する法律案"}
DOC = {"title": "ある検討会", "url": "https://example.go.jp/council/index.html"}
SOFT_WHY = "趣旨キーワード一致（検討）"


def make_gate(monkeypatch, cache, backend=None):
    monkeypatch.setattr(llm_gate, "_backend", lambda: backend)
    monkeypatch.setattr(llm_gate, "_load_cache", lambda: dict(cache))
    return llm_gate.Gate()


def test_same_bill_number_in_other_session_gets_its_own_key(monkeypatch):
    gate = make_gate(monkeypatch, {})
    assert gate._key(BILL_221, DOC) != gate._key(BILL_222, DOC)


def test_verdict_is_not_shared_across_sessions(monkeypatch):
    seed = make_gate(monkeypatch, {})
    gate = make_gate(monkeypatch, {seed._key(BILL_221, DOC): {"related": False, "reason": "無関係"}})
    gate.set_bills([BILL_221, BILL_222])

    assert gate.filter(BILL_221, [(0.7, SOFT_WHY, DOC)]) == []
    # 第222回の同番号の法案は未判定のまま（第221回の「無関係」を流用しない）
    assert len(gate.filter(BILL_222, [(0.7, SOFT_WHY, DOC)])) == 1
    assert gate.stats["unjudged"] == 1


def test_legacy_key_is_migrated_only_for_unique_bill_numbers(monkeypatch):
    seed = make_gate(monkeypatch, {})
    legacy = {
        seed._legacy_key(BILL_UNIQUE, DOC): {"related": False, "reason": "無関係"},
        seed._legacy_key(BILL_221, DOC): {"related": False, "reason": "どちらの法案か不明"},
    }
    gate = make_gate(monkeypatch, legacy)
    gate.set_bills([BILL_221, BILL_222, BILL_UNIQUE])

    # 議案番号が一意な法案は旧キーの判定を引き継ぎ、新キーへ移す
    assert gate.cached_verdict(BILL_UNIQUE, DOC) == {"related": False, "reason": "無関係"}
    assert gate._key(BILL_UNIQUE, DOC) in gate.cache
    assert gate.dirty is True
    # 番号が重複する法案は、旧キーがどちらの法案の判定か分からないので使わない
    assert gate.cached_verdict(BILL_221, DOC) is None
    assert gate.cached_verdict(BILL_222, DOC) is None


def test_legacy_key_is_ignored_until_bills_are_known(monkeypatch):
    seed = make_gate(monkeypatch, {})
    gate = make_gate(monkeypatch, {seed._legacy_key(BILL_UNIQUE, DOC): {"related": False, "reason": "x"}})
    assert gate.cached_verdict(BILL_UNIQUE, DOC) is None


def test_backend_failure_is_not_cached(monkeypatch):
    gate = make_gate(monkeypatch, {}, backend="anthropic")

    def boom(bill, doc):
        raise RuntimeError("secret-looking detail that must not be published")
    monkeypatch.setattr(llm_gate, "_judge_anthropic", boom)

    kept = gate.filter(BILL_221, [(0.7, SOFT_WHY, DOC)])

    assert len(kept) == 1                       # 保持側に倒す
    assert "LLM確認" not in kept[0][1]          # 判定していないので注記は付けない
    assert "secret" not in kept[0][1]
    assert gate.cache == {} and gate.dirty is False
    assert gate.stats["failed"] == 1


def test_failed_judgement_is_dropped_when_a_verdict_is_required(monkeypatch):
    gate = make_gate(monkeypatch, {}, backend="anthropic")
    monkeypatch.setattr(llm_gate, "_judge_anthropic", lambda bill, doc: None)
    assert gate.filter(BILL_221, [(0.7, "意味類似・要確認候補", DOC)], require_verdict=True) == []
    assert gate.cache == {}


def test_successful_judgement_is_cached_under_the_id_key(monkeypatch):
    gate = make_gate(monkeypatch, {}, backend="anthropic")
    monkeypatch.setattr(llm_gate, "_judge_anthropic",
                        lambda bill, doc: {"related": True, "reason": "関連する"})
    kept = gate.filter(BILL_221, [(0.7, SOFT_WHY, DOC)])
    assert "LLM確認: 関連する" in kept[0][1]
    assert gate.cache == {gate._key(BILL_221, DOC): {"related": True, "reason": "関連する"}}


@pytest.mark.parametrize("raw", ["", "関連します", '{"reason": "relatedが無い"}',
                                 '{"related": "yes", "reason": "真偽値でない"}', "[1, 2]"])
def test_malformed_llm_answers_are_unjudged(raw):
    assert llm_gate._verdict(llm_gate._parse(raw)) is None


def test_wellformed_llm_answer_is_accepted():
    raw = '前置き {"related": false, "reason": "無関係"} 後置き'
    assert llm_gate._verdict(llm_gate._parse(raw)) == {"related": False, "reason": "無関係"}
