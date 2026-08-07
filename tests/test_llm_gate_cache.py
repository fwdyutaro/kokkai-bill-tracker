# -*- coding: utf-8 -*-
"""LLMゲートのキャッシュのみモード。

CI（Ollama不在・APIキー無し）でゲートを丸ごと無効化すると、過去に「無関係」と
判定済みの紐付けが毎回復活して公開される。バックエンドが無くてもキャッシュだけは
適用されることを担保する。
"""
import json

import pytest

import llm_gate


BILL = {"no": "閣法 第34号", "title": "郵便法の一部を改正する法律案", "summary": ""}
DOC_BAD = {"title": "モバイル市場の競争環境に関する研究会",
           "url": "https://example.go.jp/mobile/index.html"}
DOC_UNKNOWN = {"title": "未判定の検討会", "url": "https://example.go.jp/unknown/index.html"}


@pytest.fixture
def no_backend(monkeypatch):
    """バックエンドに到達できないCI相当の状態にする。"""
    monkeypatch.setattr(llm_gate, "_backend", lambda: None)


def _seed_cache(monkeypatch, entries):
    monkeypatch.setattr(llm_gate, "_load_cache", lambda: dict(entries))


def test_gate_is_available_with_cache_only(monkeypatch, no_backend):
    _seed_cache(monkeypatch, {"dummy": {"related": False, "reason": "x"}})
    gate = llm_gate.Gate()
    assert gate.available() is True
    assert gate.cache_only() is True


def test_gate_is_unavailable_without_backend_and_cache(monkeypatch, no_backend):
    _seed_cache(monkeypatch, {})
    assert llm_gate.Gate().available() is False


def test_cached_exclusion_applies_without_backend(monkeypatch, no_backend):
    _seed_cache(monkeypatch, {})
    key = llm_gate.Gate()._key(BILL, DOC_BAD)
    _seed_cache(monkeypatch, {key: {"related": False, "reason": "無関係"}})

    gate = llm_gate.Gate()
    kept = gate.filter(BILL, [(0.8, "所管一致＋関連語（競争環境）", DOC_BAD)])

    assert kept == []
    assert gate.stats["drop"] == 1


def test_unjudged_candidate_is_kept_and_not_cached(monkeypatch, no_backend):
    _seed_cache(monkeypatch, {"dummy": {"related": True, "reason": ""}})
    gate = llm_gate.Gate()

    kept = gate.filter(BILL, [(0.7, "趣旨キーワード一致（郵便）", DOC_UNKNOWN)])

    assert len(kept) == 1
    # 判定していないので LLM 確認の注記は付かない
    assert "LLM確認" not in kept[0][1]
    assert gate.stats["unjudged"] == 1
    # 偽の判定でキャッシュを汚さない
    assert gate.dirty is False


def test_strong_evidence_bypasses_the_gate(monkeypatch, no_backend):
    _seed_cache(monkeypatch, {"dummy": {"related": True, "reason": ""}})
    gate = llm_gate.Gate()

    kept = gate.filter(BILL, [(0.92, "法案名が一致", DOC_UNKNOWN)])

    assert len(kept) == 1
    assert gate.stats["unjudged"] == 0


def test_cache_only_mode_does_not_write_cache_file(monkeypatch, tmp_path, no_backend):
    cache_file = tmp_path / "gate_cache.json"
    monkeypatch.setattr(llm_gate, "CACHE", str(cache_file))
    _seed_cache(monkeypatch, {"dummy": {"related": True, "reason": ""}})

    gate = llm_gate.Gate()
    gate.filter(BILL, [(0.7, "趣旨キーワード一致（郵便）", DOC_UNKNOWN)])
    gate.save()

    assert not cache_file.exists()
