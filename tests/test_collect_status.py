import collect


def raw_status(shu="", san="", promulgated="", shu_committee="", san_committee="",
                shu_referred="", san_referred="", continuation=""):
    return {
        "head": {"継続区分": continuation},
        "sections": {
            "衆議院委員会等経過": {"議決": shu_committee, "本付託日": shu_referred},
            "参議院委員会等経過": {"議決": san_committee, "本付託日": san_referred},
            "衆議院本会議経過": {"議決": shu},
            "参議院本会議経過": {"議決": san},
            "その他": {"公布年月日": promulgated, "法律番号": "1"},
        },
    }


def test_both_houses_passed_is_enacted_before_promulgation():
    status, detail = collect.derive_status(raw_status("可決", "可決"), [], None)
    assert status == "成立"
    assert detail == "両院可決（公布待ち）"


def test_amended_in_first_house_then_passed_is_enacted():
    """先議院の修正議決も可決の一形態。後議院が可決すれば成立する（憲法59条1項）。"""
    status, detail = collect.derive_status(raw_status("修正", "可決"), [], None)
    assert status == "成立"
    assert detail == "両院可決（公布待ち）"


def test_one_house_passed_remains_under_deliberation():
    status, _ = collect.derive_status(raw_status("可決", ""), [], None)
    assert status == "審議中"


def test_promulgation_remains_highest_priority():
    status, detail = collect.derive_status(
        raw_status("可決", "可決", "令和8年7月11日"), [], "令和8年7月11日"
    )
    assert status == "成立"
    assert detail == "公布済（法律第1号）"


# --- 審査未了（会期末） ---------------------------------------------------

def test_committee_result_miryo_becomes_unfinished():
    """参議院サイトは審査未了を「未了」と記録する。閉会情報が無くても判定できる。"""
    status, detail = collect.derive_status(
        raw_status(san_committee="未了", san_referred="令和8年7月7日"), [], None)
    assert status == "審査未了"
    assert detail == "審査未了（閉会）"


def test_closed_session_without_referral_becomes_unfinished():
    """委員会付託前のまま閉会した議案。サイトは空欄なので閉会情報で判定する。"""
    status, detail = collect.derive_status(
        raw_status(), [], None, session_closed=True)
    assert status == "審査未了"
    assert detail == "審査未了（閉会・未付託）"


def test_closed_session_after_referral_reports_committee_stage():
    status, detail = collect.derive_status(
        raw_status(san_referred="令和8年7月21日"), [], None, session_closed=True)
    assert status == "審査未了"
    assert detail == "審査未了（閉会・委員会審査中）"


def test_open_session_without_referral_stays_under_deliberation():
    status, _ = collect.derive_status(raw_status(), [], None, session_closed=False)
    assert status == "審議中"


def test_continuation_wins_over_closed_session():
    status, _ = collect.derive_status(
        raw_status("継続審査", san_committee="継続審査"), [], None, session_closed=True)
    assert status == "継続審査"


def test_rejection_wins_over_closed_session():
    status, detail = collect.derive_status(
        raw_status("否決", san_committee="否決"), [], None, session_closed=True)
    assert status == "廃案"
    assert detail == "否決・廃案"


def test_enacted_wins_over_closed_session():
    status, _ = collect.derive_status(
        raw_status("可決", "可決"), [], None, session_closed=True)
    assert status == "成立"


def test_normalize_marks_unfinished_after_session_close(monkeypatch):
    """normalize は sessions.yaml を見て会期の閉会をステータスに反映する。"""
    monkeypatch.setattr(collect, "kokkai_refs", lambda *a, **k: [])
    raw = raw_status()
    raw.update({"url": "https://example.test/x.htm", "title": "テスト法律案",
                "kind_raw": "法律案（参法）", "diet": "221回", "no": "1", "pdf": None})
    raw["head"]["提出日"] = "令和8年3月19日"

    monkeypatch.setattr(collect.sessions, "is_closed_for_status",
                        lambda diet, *a, **k: True)
    assert collect.normalize(raw)["status"] == "審査未了"
    assert collect.normalize(raw)["confidence"] == 0

    monkeypatch.setattr(collect.sessions, "is_closed_for_status",
                        lambda diet, *a, **k: False)
    assert collect.normalize(raw)["status"] == "審議中"


# --- 委員会の議決結果（明細ページの欄名は「議決」） --------------------------

def test_committee_result_is_read_from_the_real_field_name():
    """参議院の明細ページの委員会欄は「議決」。タイムラインに結果が載ること。"""
    raw = raw_status(shu_committee="可決", shu_referred="令和8年11月2日")
    raw["head"]["提出日"] = "令和8年10月20日"
    raw["sections"]["衆議院委員会等経過"].update(
        {"付託委員会等": "財務金融委員会", "議決日": "令和8年11月6日"})
    timeline, _, _ = collect.build_timeline(raw)
    assert timeline[-1]["event"] == "財務金融委員会 議決"
    assert timeline[-1]["result"] == "可決"


def test_committee_passed_waits_for_plenary():
    timeline = [{"house": "衆", "event": "財務金融委員会 議決", "result": "可決"}]
    assert collect.derive_status(raw_status(shu_committee="可決"), timeline, None) == (
        "審議中", "衆・本会議採決待ち")


def test_committee_amended_waits_for_plenary():
    timeline = [{"house": "衆", "event": "財務金融委員会 議決", "result": "修正"}]
    assert collect.derive_status(raw_status(shu_committee="修正"), timeline, None) == (
        "審議中", "衆・本会議採決待ち")


def test_committee_rejection_alone_is_not_scrapped():
    """委員会否決でも本会議で採決される。本会議の否決までは廃案にしない。"""
    timeline = [{"house": "衆", "event": "財務金融委員会 議決", "result": "否決"}]
    assert collect.derive_status(raw_status(shu_committee="否決"), timeline, None) == (
        "審議中", "衆・委員会否決（本会議採決待ち）")


def test_committee_rejection_then_plenary_rejection_is_scrapped():
    status, detail = collect.derive_status(
        raw_status("否決", shu_committee="否決"), [], None)
    assert (status, detail) == ("廃案", "否決・廃案")


def test_committee_continuation_is_continuation_even_before_plenary():
    status, _ = collect.derive_status(raw_status(shu_committee="継続審査"), [], None)
    assert status == "継続審査"
