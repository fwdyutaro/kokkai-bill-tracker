# -*- coding: utf-8 -*-
"""静的サイト用JavaScriptの共通出力。"""

from datetime import datetime
import json
from zoneinfo import ZoneInfo

import sessions as sessions_meta


JST = ZoneInfo("Asia/Tokyo")


def jst_today(now=None):
    """サイトに表示するデータ更新日をISO形式で返す。"""
    current = now or datetime.now(JST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=JST)
    return current.astimezone(JST).date().isoformat()


def render_data_js(records, updated_at=None, sessions=None):
    """window.BILLS / 更新日 / window.SESSIONS を含むJavaScriptを生成する。

    sessions を省略した場合は sessions.yaml から読み込む。呼び出し元
    （collect / merge_submissions / apply_suppressions / match_refs / tag）が
    どこであっても同じ出力になるよう、既定値はこの関数の中で解決する。
    """
    payload = json.dumps(records, ensure_ascii=False, indent=2)
    date_value = updated_at or jst_today()
    if sessions is None:
        sessions = sessions_meta.load_sessions()
    sessions_payload = json.dumps(sessions, ensure_ascii=False, indent=2)
    return (
        f"window.BILLS = {payload};\n"
        f"window.BILLS_UPDATED_AT = {json.dumps(date_value)};\n"
        f"window.SESSIONS = {sessions_payload};\n"
    )
