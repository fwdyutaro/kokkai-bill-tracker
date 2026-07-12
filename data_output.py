# -*- coding: utf-8 -*-
"""静的サイト用JavaScriptの共通出力。"""

from datetime import datetime
import json
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")


def jst_today(now=None):
    """サイトに表示するデータ更新日をISO形式で返す。"""
    current = now or datetime.now(JST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=JST)
    return current.astimezone(JST).date().isoformat()


def render_data_js(records, updated_at=None):
    """window.BILLS と更新日を含むJavaScriptを生成する。"""
    payload = json.dumps(records, ensure_ascii=False, indent=2)
    date_value = updated_at or jst_today()
    return (
        f"window.BILLS = {payload};\n"
        f"window.BILLS_UPDATED_AT = {json.dumps(date_value)};\n"
    )
