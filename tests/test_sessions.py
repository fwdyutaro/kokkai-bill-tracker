# -*- coding: utf-8 -*-
"""会期メタデータ (sessions.yaml / sessions.py) のテスト。"""
import textwrap

import pytest

import sessions


@pytest.fixture
def yaml_path(tmp_path):
    path = tmp_path / "sessions.yaml"
    path.write_text(textwrap.dedent("""\
        sessions:
          - diet: 220
            name: 常会
            from: "2025-01-24"
            to: "2025-06-22"
            clb_id: "5000"
          - diet: 221
            name: 特別会
            from: "2026-02-18"
            to: "2026-07-25"
            clb_id: "5144"
          - diet: 222
            name: 臨時会
            from: "2026-09-28"
            to: null
        """), encoding="utf-8")
    return path


def test_load_sessions_sorted_and_normalized(yaml_path):
    got = sessions.load_sessions(yaml_path, use_cache=False)
    assert [s["diet"] for s in got] == [220, 221, 222]
    assert got[1] == {"diet": 221, "name": "特別会", "from": "2026-02-18",
                      "to": "2026-07-25", "clb_id": "5144"}
    assert got[2]["to"] is None and got[2]["clb_id"] is None


def test_load_sessions_returns_empty_when_file_missing(tmp_path):
    assert sessions.load_sessions(tmp_path / "nope.yaml", use_cache=False) == []


def test_load_sessions_survives_broken_yaml(tmp_path, capsys):
    path = tmp_path / "sessions.yaml"
    path.write_text("sessions: [ this: is: broken", encoding="utf-8")
    assert sessions.load_sessions(path, use_cache=False) == []
    assert "警告" in capsys.readouterr().err


@pytest.mark.parametrize("value", [221, "221", "221回", "第221回", "第221回国会"])
def test_get_session_accepts_diet_spellings(yaml_path, value):
    assert sessions.get_session(value, yaml_path)["name"] == "特別会"


def test_get_session_returns_none_for_unknown(yaml_path):
    assert sessions.get_session(999, yaml_path) is None
    assert sessions.get_session(None, yaml_path) is None


def test_latest_diet(yaml_path):
    assert sessions.latest_diet(yaml_path) == 222


def test_clb_id_lookup(yaml_path):
    assert sessions.clb_id("221回", yaml_path) == "5144"
    assert sessions.clb_id(222, yaml_path) is None


def test_is_closed_is_false_on_the_final_day(yaml_path):
    assert sessions.is_closed(221, "2026-07-25", yaml_path) is False
    assert sessions.is_closed(221, "2026-07-26", yaml_path) is True


def test_is_closed_is_false_while_session_runs(yaml_path):
    assert sessions.is_closed(222, "2027-01-01", yaml_path) is False
    assert sessions.is_closed(999, "2027-01-01", yaml_path) is False


def test_status_judgement_is_held_back_for_a_week_after_closing(yaml_path):
    # 閉会日を1日目として7日間は判定を保留する（参議院サイトの反映遅れ対策）。
    for day in ("2026-07-25", "2026-07-26", "2026-07-30"):
        assert sessions.is_closed_for_status(221, day, yaml_path) is False
    assert sessions.is_closed_for_status(221, "2026-07-31", yaml_path) is True
    assert sessions.is_closed_for_status(221, "2026-08-05", yaml_path) is True


def test_status_judgement_never_applies_to_unregistered_or_open_sessions(yaml_path):
    assert sessions.is_closed_for_status(222, "2027-05-05", yaml_path) is False
    assert sessions.is_closed_for_status(999, "2027-05-05", yaml_path) is False


def test_is_open_covers_convocation_and_closing(yaml_path):
    assert sessions.is_open(222, "2026-09-27", yaml_path) is False   # 召集前
    assert sessions.is_open(222, "2026-09-28", yaml_path) is True
    assert sessions.is_open(221, "2026-07-25", yaml_path) is True    # 最終日は開会中
    assert sessions.is_open(221, "2026-07-26", yaml_path) is False


def test_has_open_session(yaml_path):
    assert sessions.has_open_session("2026-07-01", yaml_path) is True
    assert sessions.has_open_session("2026-08-01", yaml_path) is False
    assert sessions.has_open_session("2026-10-01", yaml_path) is True


def test_should_run_full_daily_while_in_session(yaml_path):
    run, reason = sessions.should_run_full("2026-07-01", path=yaml_path)
    assert run is True and "開会中" in reason


def test_should_run_full_weekly_when_out_of_session(yaml_path):
    # 2026-08-01 は土曜（週次実行日）、2026-08-02 は日曜。
    assert sessions.should_run_full("2026-08-01", path=yaml_path)[0] is True
    assert sessions.should_run_full("2026-08-02", path=yaml_path)[0] is False


def test_should_run_full_forced_by_manual_dispatch(yaml_path):
    run, reason = sessions.should_run_full("2026-08-02", force=True, path=yaml_path)
    assert run is True and "手動" in reason


def test_repository_sessions_yaml_registers_the_current_diet():
    """同梱の sessions.yaml が第221回を閉会済みとして持っていること。"""
    s = sessions.get_session(221)
    assert s is not None
    assert s["to"] == "2026-07-25"
    assert s["clb_id"] == "5144"


def test_should_run_cli_prints_github_output(capsys):
    assert sessions._main(["should-run", "--force"]) == 0
    assert capsys.readouterr().out.strip() == "run=true"
