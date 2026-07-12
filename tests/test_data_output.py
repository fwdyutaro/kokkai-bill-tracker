from datetime import datetime, timezone

from data_output import jst_today, render_data_js


def test_jst_today_uses_tokyo_date():
    utc = datetime(2026, 7, 11, 21, 0, tzinfo=timezone.utc)
    assert jst_today(utc) == "2026-07-12"


def test_render_data_js_includes_bills_and_update_date():
    text = render_data_js([{"id": "x"}], updated_at="2026-07-11")
    assert text.startswith("window.BILLS = [")
    assert 'window.BILLS_UPDATED_AT = "2026-07-11";' in text
