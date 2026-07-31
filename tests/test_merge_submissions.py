# -*- coding: utf-8 -*-
"""複数会期を保持する bills.json での提供情報の紐付けテスト。"""
import merge_submissions as M


def bill(diet, no):
    return {"id": f"{diet}-閣法-{no}", "diet": f"{diet}回",
            "no": f"閣法 第{no}号", "refs": []}


def test_index_by_no_prefers_the_latest_diet():
    """議案番号は会期をまたいで衝突する。最新会期のレコードを採ること。"""
    bills = [bill(221, 31), bill(222, 31), bill(221, 5)]
    index = M.index_by_no(bills)
    assert index["閣法 第31号"]["id"] == "222-閣法-31"
    assert index["閣法 第5号"]["id"] == "221-閣法-5"


def test_index_by_no_is_order_independent():
    index = M.index_by_no([bill(222, 31), bill(221, 31)])
    assert index["閣法 第31号"]["id"] == "222-閣法-31"


def test_diet_of_falls_back_to_id_prefix():
    assert M._diet_of({"id": "222-閣法-1"}) == 222
    assert M._diet_of({"id": "壊れたid"}) == -1
