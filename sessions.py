# -*- coding: utf-8 -*-
"""
会期メタデータ (sessions.yaml) の読み込みユーティリティ。

参議院サイトは会期終了時、審査未了の議案を「議決・継続結果 = 未了」と記録するが、
委員会付託前の議案は提出日以外すべて空欄のままになる。つまりサイトのデータだけでは
「審議中」と「閉会により審査未了」を区別できない。会期の閉会日という外部情報が要る。
そのための単一の情報源が sessions.yaml で、本モジュールがその読み口になる。

  from sessions import load_sessions, get_session, is_closed, latest_diet
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

JST = ZoneInfo("Asia/Tokyo")
SESSIONS_PATH = Path(__file__).resolve().parent / "sessions.yaml"

# 閉会直後は参議院サイトの反映が追いつかず、未了・継続の記録が空欄のことがある。
# そこで閉会日を1日目として7日間は「閉会だから審査未了」という推定を保留し、
# 7日目（閉会日+6日）以降に有効化する。明示的な「未了」記録はこの猶予の対象外。
CLOSE_GRACE_DAYS = 7

_CACHE: dict[str, object] = {}


class SessionConfigError(ValueError):
    """sessions.yaml が存在しない、またはメタデータが不正。"""


def _config_error(message):
    print(f"警告: {message}", file=sys.stderr)
    raise SessionConfigError(message)


def jst_today(today=None) -> date:
    """引数を date に正規化する。未指定ならJSTの今日。"""
    if today is None:
        return datetime.now(JST).date()
    if isinstance(today, datetime):
        return today.astimezone(JST).date() if today.tzinfo else today.date()
    if isinstance(today, date):
        return today
    return date.fromisoformat(str(today))


def normalize_diet(diet) -> str | None:
    """221 / "221" / "221回" / "第221回" → "221"。数字が無ければ None。"""
    if diet is None:
        return None
    digits = "".join(ch for ch in str(diet) if ch.isdigit())
    return str(int(digits)) if digits else None


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _strict_date(value, field, index):
    if isinstance(value, datetime) or not isinstance(value, str):
        _config_error(f"session[{index}] {field} は ISO 日付が必要です")
    value = value.strip()
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        _config_error(f"session[{index}] {field} は ISO 日付が必要です")
    if parsed.isoformat() != value:
        _config_error(f"session[{index}] {field} は ISO 日付が必要です")
    return parsed


def load_sessions(path=None, use_cache=True) -> list[dict]:
    """sessions.yaml を読んで会期dictのリストを返す。欠落・不正時はSessionConfigError。

    返る各dictは {"diet": int, "name": str, "from": str|None,
                  "to": str|None, "clb_id": str|None} 形式（JSON化可能）。
    """
    target = Path(path) if path else SESSIONS_PATH
    key = str(target)
    if use_cache and key in _CACHE:
        return [dict(s) for s in _CACHE[key]]  # 呼び出し側の書き換えから守る
    if not target.exists():
        _config_error(f"sessions metadata not found: {target}")
    try:
        with target.open(encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"warning: failed to parse {target}: {e}", file=sys.stderr)
        _config_error(f"invalid YAML: {target}")
    raw = doc.get("sessions") if isinstance(doc, dict) else None
    if not isinstance(raw, list):
        _config_error(f"invalid sessions metadata: {target}")
    sessions: list[dict] = []
    seen = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            _config_error(f"session[{index}] must be a mapping")
        raw_diet = item.get("diet")
        if isinstance(raw_diet, bool):
            _config_error(f"session[{index}] diet is invalid")
        diet_key = normalize_diet(raw_diet)
        if diet_key is None:
            _config_error(f"session[{index}] diet is invalid")
        diet = int(diet_key)
        if diet <= 0 or diet in seen:
            _config_error(f"session[{index}] diet is invalid or duplicated")
        seen.add(diet)
        start = _strict_date(item.get("from"), "from", index)
        raw_end = item.get("to")
        end = None if raw_end is None else _strict_date(raw_end, "to", index)
        if end is not None and end < start:
            _config_error(f"session[{index}] to precedes from")
        sessions.append({
            "diet": diet,
            "name": item.get("name") or "",
            "from": start.isoformat(),
            "to": end.isoformat() if end else None,
            "clb_id": str(item["clb_id"]) if item.get("clb_id") else None,
        })
    sessions.sort(key=lambda s: s["diet"])
    if use_cache:
        _CACHE[key] = [dict(s) for s in sessions]
    return sessions


def get_session(diet, path=None) -> dict | None:
    """会期番号に対応する会期dictを返す。未登録なら None。"""
    key = normalize_diet(diet)
    if key is None:
        return None
    for s in load_sessions(path):
        if str(s["diet"]) == key:
            return s
    return None


def latest_diet(path=None) -> int | None:
    """登録済み会期のうち最大の回次。1件も無ければ None。"""
    sessions = load_sessions(path)
    return max((s["diet"] for s in sessions), default=None) if sessions else None


def session_end(diet, path=None) -> date | None:
    """閉会日。会期中（to が null）や未登録なら None。"""
    s = get_session(diet, path)
    return _parse_date(s["to"]) if s else None


def is_closed(diet, today=None, path=None) -> bool:
    """会期終了日を過ぎているか。未登録・会期中は False。"""
    end = session_end(diet, path)
    return bool(end and jst_today(today) > end)


def is_closed_for_status(diet, today=None, path=None,
                         grace_days=CLOSE_GRACE_DAYS) -> bool:
    """ステータス判定に閉会を反映してよいか（猶予期間つきの is_closed）。

    参議院サイトの更新遅れで誤判定しないよう、閉会日を1日目として
    grace_days 日間は False を返す（=「審議中」のまま据え置く）。
    """
    end = session_end(diet, path)
    if not end:
        return False
    return jst_today(today) >= end + timedelta(days=max(grace_days - 1, 0))


def is_open(diet, today=None, path=None) -> bool:
    """その会期が開会中か（召集済みかつ未閉会）。"""
    s = get_session(diet, path)
    if not s:
        return False
    now = jst_today(today)
    start, end = _parse_date(s["from"]), _parse_date(s["to"])
    if start and now < start:
        return False        # 召集前
    return end is None or now <= end


def open_sessions(today=None, path=None) -> list[dict]:
    """開会中の会期の一覧。"""
    return [s for s in load_sessions(path) if is_open(s["diet"], today, path)]


def has_open_session(today=None, path=None) -> bool:
    """開会中の会期が1つでもあるか（CIの実行頻度判定に使う）。"""
    return bool(open_sessions(today, path))


def clb_id(diet, path=None) -> str | None:
    """内閣法制局 一覧ページID。未登録なら None。"""
    s = get_session(diet, path)
    return s["clb_id"] if s else None


# 閉会中にフルパイプラインを回す曜日（JST基準・月曜=0）。
# 土曜JST 01:01 = 金曜UTC 16:01 で、既存cron("1 16 * * 1-6")に含まれる。
WEEKLY_JST_WEEKDAY = 5


def should_run_full(today=None, force=False, path=None,
                    weekly_weekday=WEEKLY_JST_WEEKDAY):
    """CIでフルパイプラインを走らせるべきか判定し、(bool, 理由) を返す。"""
    if force:
        return True, "手動実行のため常にフル実行"
    if has_open_session(today, path):
        names = "・".join(f"第{s['diet']}回{s['name']}" for s in open_sessions(today, path))
        return True, f"開会中の会期あり（{names}）"
    now = jst_today(today)
    if now.weekday() == weekly_weekday:
        return True, f"閉会中だが週次実行日（JST {now.isoformat()}）"
    return False, f"閉会中かつ週次実行日ではない（JST {now.isoformat()}）"


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="会期メタデータの参照")
    ap.add_argument("command", choices=["status", "should-run", "latest"],
                    help="status=一覧表示 / should-run=CI用ゲート / latest=最新回次")
    ap.add_argument("--force", action="store_true", help="should-run を常に true にする")
    ap.add_argument("--today", default=None, help="判定日 (YYYY-MM-DD)。既定はJSTの今日")
    args = ap.parse_args(argv)
    today = args.today or None

    if args.command == "latest":
        latest = latest_diet()
        print(latest if latest is not None else "")
        return 0
    if args.command == "status":
        for s in load_sessions():
            state = "開会中" if is_open(s["diet"], today) else "閉会"
            print(f"第{s['diet']}回 {s['name']}  {s['from']} 〜 {s['to'] or '（会期中）'}  {state}")
        return 0
    run, reason = should_run_full(today, force=args.force)
    print(reason, file=sys.stderr)
    print(f"run={'true' if run else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
