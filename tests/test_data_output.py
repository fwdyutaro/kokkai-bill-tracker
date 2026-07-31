import json
from datetime import datetime, timezone

from data_output import jst_today, render_data_js


def test_jst_today_uses_tokyo_date():
    utc = datetime(2026, 7, 11, 21, 0, tzinfo=timezone.utc)
    assert jst_today(utc) == "2026-07-12"


def test_render_data_js_includes_bills_and_update_date():
    text = render_data_js([{"id": "x"}], updated_at="2026-07-11")
    assert text.startswith("window.BILLS = [")
    assert 'window.BILLS_UPDATED_AT = "2026-07-11";' in text


def test_render_data_js_emits_sessions_from_yaml_by_default():
    """呼び出し元によらず同じ出力になるよう、既定の会期は関数内で解決する。"""
    text = render_data_js([{"id": "x"}], updated_at="2026-07-11")
    assert "window.SESSIONS = [" in text
    sessions = json.loads(text.split("window.SESSIONS = ", 1)[1].rstrip().rstrip(";"))
    assert any(s["diet"] == 221 and s["to"] == "2026-07-25" for s in sessions)


def test_render_data_js_accepts_explicit_sessions():
    text = render_data_js([], updated_at="2026-07-11",
                          sessions=[{"diet": 222, "name": "臨時会", "to": None}])
    assert '"diet": 222' in text
