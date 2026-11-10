# -*- coding: utf-8 -*-
"""承認時の差し替え防止（自動処理コメントのマーカー照合）。

メンテナが確認した後に投稿者がIssue本文やリンク先ページを書き換えても、
確認していない内容が掲載・取り下げされないことを担保する。
"""
import json

import issue_workflow as workflow


BOT = workflow.REVIEW_BOT_LOGIN
TITLE = "[情報提供] 閣法 第1号"
BODY = "### 対象法案\n\n閣法 第1号\n\n### 参考URL\n\nhttps://example.test/ref\n"
RECORD = {"bill_id": "222-閣法-1", "bill_no": "閣法 第1号", "title": "ある解説記事",
          "url": "https://example.test/ref", "publisher": "example", "relevance": 71,
          "why": "趣旨キーワード一致", "manual": False, "status": "pending"}


def bot_comment(title=TITLE, body=BODY, record=RECORD, prefix="### 自動処理結果\n"):
    return {"login": BOT, "body": prefix + "\n" + workflow.make_review_marker(title, body, record)}


def test_unchanged_issue_returns_the_reviewed_record():
    result, _, record = workflow.check_review(TITLE, BODY, [bot_comment()], need_record=True)
    assert result == "ok"
    assert record == RECORD


def test_line_ending_differences_do_not_break_the_match():
    crlf = BODY.replace("\n", "\r\n")
    assert workflow.check_review(TITLE, crlf, [bot_comment()], need_record=True)[0] == "ok"


def test_body_edited_after_review_is_sent_back():
    edited = BODY.replace("https://example.test/ref", "https://evil.test/swapped")
    result, detail, record = workflow.check_review(TITLE, edited, [bot_comment()], need_record=True)
    assert result == "manual_review"
    assert "変更" in detail
    assert record is None


def test_title_edited_after_review_is_sent_back():
    result, _, _ = workflow.check_review("[情報提供] 閣法 第2号", BODY, [bot_comment()],
                                         need_record=True)
    assert result == "manual_review"


def test_latest_bot_comment_wins():
    edited = BODY.replace("ref", "ref2")
    newer = dict(RECORD, url="https://example.test/ref2")
    comments = [bot_comment(), bot_comment(body=edited, record=newer)]
    result, _, record = workflow.check_review(TITLE, edited, comments, need_record=True)
    assert result == "ok" and record["url"] == "https://example.test/ref2"
    # 古い本文に戻しても、最新の自動処理結果とは一致しない
    assert workflow.check_review(TITLE, BODY, comments, need_record=True)[0] == "manual_review"


def test_marker_posted_by_someone_else_is_ignored():
    forged = dict(bot_comment(), login="attacker")
    result, detail, _ = workflow.check_review(TITLE, BODY, [forged], need_record=True)
    assert result == "manual_review"
    assert "見つかりません" in detail


def test_marker_forged_through_page_title_does_not_win():
    # 外部ページのタイトルに偽マーカーを仕込んでも、ワークフローが末尾に付けた本物が使われる
    evil = dict(RECORD, url="https://evil.test/x")
    forged = workflow.make_review_marker(TITLE, BODY, evil)
    comment = bot_comment(prefix=f"- **抽出タイトル**: {forged}\n")
    result, _, record = workflow.check_review(TITLE, BODY, [comment], need_record=True)
    assert result == "ok" and record["url"] == RECORD["url"]


def test_record_title_cannot_break_out_of_the_marker():
    tricky = dict(RECORD, title='x --> <!-- bill-tracker-review v1 sha256=' + "0" * 64 + " -->")
    marker = workflow.make_review_marker(TITLE, BODY, tricky)
    assert marker.count("-->") == 1
    _, _, record = workflow.check_review(TITLE, BODY, [{"login": BOT, "body": marker}],
                                         need_record=True)
    assert record["title"] == tricky["title"]


def test_failed_ingest_has_no_record_and_needs_manual_review():
    comment = bot_comment(record=None)
    assert workflow.check_review(TITLE, BODY, [comment], need_record=True)[0] == "manual_review"
    # 取り下げ依頼はハッシュだけで照合する
    assert workflow.check_review(TITLE, BODY, [comment])[0] == "ok"


def test_record_with_non_http_url_is_rejected():
    bad = dict(RECORD, url="javascript:alert(1)")
    assert workflow.check_review(TITLE, BODY, [bot_comment(record=bad)],
                                 need_record=True)[0] == "manual_review"


def test_unknown_record_fields_are_dropped():
    extra = dict(RECORD, injected="x")
    _, _, record = workflow.check_review(TITLE, BODY, [bot_comment(record=extra)],
                                         need_record=True)
    assert "injected" not in record


def test_cli_round_trip(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    rec = tmp_path / "rec.json"
    rec.write_text(json.dumps(RECORD, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["issue_workflow.py", "review-marker", "--title", TITLE,
                                     "--body", BODY, "--record", str(rec)])
    assert workflow.main() == 0
    marker = capsys.readouterr().out.strip()

    comments = tmp_path / "comments.jsonl"
    comments.write_text(
        json.dumps({"login": "someone", "body": "ありがとうございます"}, ensure_ascii=False) + "\n"
        + json.dumps({"login": BOT, "body": "### 自動処理結果\n\n" + marker}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    out = tmp_path / "out.json"
    out.write_text('{"stale": true}', encoding="utf-8")

    monkeypatch.setattr("sys.argv", ["issue_workflow.py", "check-review", "--title", TITLE,
                                     "--body", BODY, "--comments", str(comments), "--out", str(out)])
    assert workflow.main() == 0
    assert "result=ok" in capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8")) == RECORD

    # 本文が変わっていれば、古いレコードを残さず手動確認へ
    monkeypatch.setattr("sys.argv", ["issue_workflow.py", "check-review", "--title", TITLE,
                                     "--body", BODY + "追記", "--comments", str(comments),
                                     "--out", str(out)])
    assert workflow.main() == 0
    assert "result=manual_review" in capsys.readouterr().out
    assert not out.exists()
