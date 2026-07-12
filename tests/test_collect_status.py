import collect


def raw_status(shu="", san="", promulgated=""):
    return {
        "head": {},
        "sections": {
            "衆議院委員会等経過": {},
            "参議院委員会等経過": {},
            "衆議院本会議経過": {"議決": shu},
            "参議院本会議経過": {"議決": san},
            "その他": {"公布年月日": promulgated, "法律番号": "1"},
        },
    }


def test_both_houses_passed_is_enacted_before_promulgation():
    status, detail = collect.derive_status(raw_status("可決", "可決"), [], None)
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
