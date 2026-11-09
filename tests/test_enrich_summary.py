# -*- coding: utf-8 -*-
"""Ollama 要約の失敗理由が build.log に残ること。

以前は失敗を黙って None にしていたため、「要約未生成」の原因をログから追えなかった。
"""
import pytest
import requests

import enrich


class _Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(enrich, "_ollama_unreachable_reported", False)


def _post_returning(monkeypatch, resp=None, exc=None):
    def post(*a, **k):
        if exc:
            raise exc
        return resp
    monkeypatch.setattr(enrich.requests, "post", post)


def test_success_returns_text(monkeypatch, capsys):
    _post_returning(monkeypatch, _Resp(body={"response": " 要約です \n"}))
    assert enrich._ollama_summarize("p", "法案A") == "要約です"
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("resp, expected", [
    (_Resp(status=500, text="model crashed"), "HTTP 500 model crashed"),
    (_Resp(body={"response": "", "done_reason": "length", "eval_count": 300}),
     "空の応答（done_reason=length, eval_count=300）"),
    (_Resp(text="<html>"), "JSONでない応答"),
])
def test_failures_are_reported_with_reason(monkeypatch, capsys, resp, expected):
    _post_returning(monkeypatch, resp)
    assert enrich._ollama_summarize("p", "法案A") is None
    err = capsys.readouterr().err
    assert "Ollama要約失敗 法案A" in err and expected in err


def test_timeout_is_reported(monkeypatch, capsys):
    _post_returning(monkeypatch, exc=requests.exceptions.ReadTimeout("read timed out"))
    assert enrich._ollama_summarize("p", "法案A") is None
    assert "ReadTimeout" in capsys.readouterr().err


def test_unreachable_ollama_is_reported_once(monkeypatch, capsys):
    _post_returning(monkeypatch, exc=requests.exceptions.ConnectionError("refused"))
    for _ in range(3):
        assert enrich._ollama_summarize("p", "法案A") is None
    assert capsys.readouterr().err.count("Ollama に接続できません") == 1
