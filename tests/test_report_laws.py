# -*- coding: utf-8 -*-
"""審議会・検討会の報告書が言及する法令名による紐付け（report_laws.py ほか）のテスト。"""
import sqlite3
from datetime import date

from bs4 import BeautifulSoup

import crawl
import llm_gate
import match_refs
import report_laws as R


# --- 法令名の抽出 ---------------------------------------------------------

def test_amend_context_is_counted_but_citation_is_not():
    text = ("当該権限については、警察官職務執行法の中に規定することが考えられる。"
            "また、警察官職務執行法第三条の規定に基づき保護する。")
    m = R.law_mentions(text)
    assert m["laws"]["警察官職務執行法"] == 2
    assert m["amend"] == {"警察官職務執行法": 1}


def test_various_amend_phrases():
    for phrase in ("刑事訴訟法を改正し", "刑事訴訟法の一部を改正する", "刑事訴訟法に新たに規定を設け",
                   "刑事訴訟法に盛り込む", "刑事訴訟法改正案"):
        assert R.law_mentions(phrase)["amend"] == {"刑事訴訟法": 1}, phrase


def test_abbreviation_counts_for_the_formal_name():
    text = ("重要施設の周辺地域の上空における小型無人機等の飛行の禁止に関する法律"
            "（平成28年法律第9号。以下「小型無人機等飛行禁止法」という。）について、"
            "小型無人機等飛行禁止法を改正し、対象施設を拡大する。")
    formal = "重要施設の周辺地域の上空における小型無人機等の飛行の禁止に関する法律"
    m = R.law_mentions(text)
    assert R.mentions_law(m["amend"], formal) == 1
    assert "小型無人機等飛行禁止法" not in m["laws"]


def test_non_law_words_are_ignored():
    m = R.law_mentions("遠隔解析の手法と方法を検討し、法人の規定と法令を整理する。違法な行為。")
    assert m["laws"] == {}


def test_long_title_requires_title_shape():
    m = R.law_mentions("この法律は、ストーカー行為等の規制等に関する法律を改正するものである。")
    assert "この法律" not in "".join(m["laws"])
    assert R.mentions_law(m["amend"], "ストーカー行為等の規制等に関する法律") == 1


# --- 件名 → 改正対象法令 ---------------------------------------------------

def test_amended_laws_from_titles():
    cases = {
        "警察官職務執行法の一部を改正する法律案": ["警察官職務執行法"],
        "外国為替及び外国貿易法の一部を改正する法律案": ["外国為替及び外国貿易法"],
        "所得税法等の一部を改正する法律案": ["所得税法"],
        "民法等の一部を改正する法律案": ["民法"],
        "特別職の職員の給与に関する法律及び行政機関の職員の定員に関する法律の一部を改正する法律案":
            ["特別職の職員の給与に関する法律", "行政機関の職員の定員に関する法律"],
        "令和八年度における公債の発行の特例に関する法律案":
            ["令和八年度における公債の発行の特例に関する法律"],
    }
    for title, laws in cases.items():
        assert R.amended_laws(title) == laws, title


def test_mentions_law_matches_prefixed_tokens_only_for_long_names():
    assert R.mentions_law({"改正警察官職務執行法": 2}, "警察官職務執行法") == 2
    assert R.mentions_law({"国民法": 1}, "民法") == 0
    assert R.mentions_law({"民法": 1}, "民法") == 1


def test_update_cache_records_failures_and_skips_known(tmp_path):
    con = sqlite3.connect(":memory:")
    crawl.db_init(con)
    src = {"id": "s", "tier": 1, "category": "審議会・検討会", "publisher": "警察庁"}
    crawl.upsert(con, src, "提言", "https://x.test/a.pdf", "")
    crawl.upsert(con, src, "提言2", "https://x.test/b.pdf", "")
    cache = {"https://x.test/a.pdf": {"laws": {}, "amend": {}, "ok": True}}
    fetched = []
    n = R.update_cache(con, lambda u: fetched.append(u) or b"<html>", cache, sleep=0,
                       today=date(2026, 11, 8))
    assert n == 1 and fetched == ["https://x.test/b.pdf"]
    assert cache["https://x.test/b.pdf"] == {"laws": {}, "amend": {}, "ok": False,
                                             "checked": "2026-11-08"}


# --- 収集（会議一覧 → 会議ページ） --------------------------------------------

INDEX = """<main><a href="/council/a.html">匿名・流動型犯罪グループ対策に関する有識者検討会</a>
<a href="/council/b.html">ストーカー行為等の規制等の在り方に関する有識者検討会（令和８年～）</a>
<a href="/policies/council/index.html">懇談会等・有識者会議 一覧</a>
<a href="https://other.example/c.html">他サイトの研究会のページ</a>
<a href="/council/x.pdf">検討会の資料一覧PDF</a></main>"""

PAGE_A = """<main><h1>有識者検討会</h1>
<p>構成員一覧 <a href="kouseiin.pdf">構成員一覧</a></p>
<p>提言骨子［<a href="kosshi.pdf">本文</a>／<a href="kosshi_g.pdf">概要</a>]</p>
<p>提言［<a href="teigen.pdf">本文</a>／<a href="teigen_g.pdf">概要</a>]</p>
<section><h2>第１回（令和８年８月10日）</h2>
<ul><li><a href="1gijishidai.pdf">議事次第</a></li><li><a href="1shiryou1.pdf">資料１</a></li></ul>
</section></main>"""

PAGE_B = """<main><h2>ストーカー行為等の規制等の在り方に関する有識者検討会（令和８年～）</h2>
<h3>第１回検討会（令和８年10月19日）</h3><ul><li><a href="r8/1shiryou.pdf">資料１</a></li></ul>
<h2>ストーカー行為等の規制等の在り方に関する有識者検討会（令和２年～３年）</h2>
<h3>第１回検討会</h3><ul><li><a href="r2/gijiyoushi.pdf">議事要旨（149KB）</a></li></ul>
<h3>ストーカー行為等の規制等の在り方に関する報告書</h3>
<ul><li><a href="r2/houkokusyo.pdf">報告書（544KB）</a></li></ul>
<h2>ストーカー加害者に対する精神医学的アプローチに関する調査研究</h2>
<ul><li><a href="r4/report.pdf">令和４年度調査研究報告書 全文 (1.41MB)</a></li></ul></main>"""


def test_council_links_keep_same_site_meeting_pages_only():
    soup = BeautifulSoup(INDEX, "html.parser")
    links = crawl.council_links(soup, "https://www.npa.go.jp/policies/council/index.html")
    assert [u for _, u in links] == ["https://www.npa.go.jp/council/a.html",
                                     "https://www.npa.go.jp/council/b.html"]


def test_report_pdfs_use_the_paragraph_lead_as_label():
    soup = BeautifulSoup(PAGE_A, "html.parser")
    got = [(label, href.rsplit("/", 1)[1])
           for label, href, _ in crawl.report_pdfs(soup, "https://www.npa.go.jp/council/a.html")]
    assert got == [("提言骨子・本文", "kosshi.pdf"), ("提言骨子・概要", "kosshi_g.pdf"),
                   ("提言・本文", "teigen.pdf"), ("提言・概要", "teigen_g.pdf")]


def test_follow_mode_attributes_reports_to_their_section(monkeypatch):
    pages = {"https://www.npa.go.jp/policies/council/index.html": INDEX,
             "https://www.npa.go.jp/council/a.html": PAGE_A,
             "https://www.npa.go.jp/council/b.html": PAGE_B}
    monkeypatch.setattr(crawl, "get_html", lambda url: pages[url])
    con = sqlite3.connect(":memory:")
    crawl.db_init(con)
    src = {"id": "npa", "tier": 1, "category": "審議会・検討会", "publisher": "警察庁",
           "ministry": "警察庁", "method": "ministry_index", "mode": "follow", "sleep": 0,
           "url": "https://www.npa.go.jp/policies/council/index.html"}
    crawl.crawl_ministry_index(con, src)
    rows = {u.rsplit("/", 1)[-1]: (t, p) for t, u, p in
            con.execute("SELECT title, url, parent_url FROM documents")}
    # 会議名は一覧のリンク文字（ページの h1「有識者検討会」ではない）
    assert rows["a.html"][0] == "匿名・流動型犯罪グループ対策に関する有識者検討会"
    assert rows["teigen.pdf"] == ("匿名・流動型犯罪グループ対策に関する有識者検討会（提言・本文）",
                                  "https://www.npa.go.jp/council/a.html")
    # 議事次第・資料・構成員一覧は報告書として取り込まない
    assert not {"1gijishidai.pdf", "1shiryou1.pdf", "kouseiin.pdf", "1shiryou.pdf"} & set(rows)
    # 令和2年の報告書は令和2年～3年の検討会（ページ内アンカー）に属する
    title, parent = rows["houkokusyo.pdf"]
    assert title == "ストーカー行為等の規制等の在り方に関する有識者検討会（令和２年～３年）（報告書）"
    assert parent.startswith("https://www.npa.go.jp/council/b.html#sec")
    # 会議名でない区画の報告書は見出しを名前に、紐付け先はページ全体
    title, parent = rows["report.pdf"]
    assert title.startswith("ストーカー加害者に対する精神医学的アプローチに関する調査研究（")
    assert parent == "https://www.npa.go.jp/council/b.html"
    # 「第１回検討会」は会議として登録しない
    assert not any(t.startswith("第１回") for t, _ in rows.values())


# --- 照合 -------------------------------------------------------------------

def _doc(url, title, ministry="警察庁", parent=None):
    return {"tier": 1, "category": "審議会・検討会", "publisher": ministry, "title": title,
            "url": url, "published_at": "", "ministry": ministry, "abstract": None,
            "parent_url": parent}


def _index(docs, cache):
    return match_refs.build_report_index(docs, cache)


BILL = {"no": "閣法 第15号", "title": "警察官職務執行法の一部を改正する法律案",
        "ministry": "警察庁", "submittedOn": "2026-10-16"}


def _council(n, law="警察官職務執行法", ministry="警察庁"):
    page = f"https://npa.test/{n}.html"
    docs = [_doc(page, f"検討会{n}", ministry),
            _doc(f"https://npa.test/{n}_kosshi.pdf", f"検討会{n}（提言骨子・本文）", ministry, page),
            _doc(f"https://npa.test/{n}_teigen.pdf", f"検討会{n}（提言・本文）", ministry, page)]
    cache = {docs[1]["url"]: {"laws": {law: 9}, "amend": {law: 3}},
             docs[2]["url"]: {"laws": {law: 5}, "amend": {law: 1}}}
    return docs, cache


def test_law_mention_attaches_council_page_and_final_report():
    docs, cache = _council(1)
    mcache = {"https://npa.test/1.html": "2026-09"}
    got = match_refs.law_mention_candidates(BILL, _index(docs, cache), mcache)
    assert [d["url"] for _, _, d in got] == ["https://npa.test/1.html", "https://npa.test/1_teigen.pdf"]
    assert all("改正対象の法令に言及（警察官職務執行法）" in why for _, why, _ in got)
    assert all(any(s in why for s in llm_gate.SOFT) for _, why, _ in got)   # LLMゲートの対象
    assert "改正・規定に4回" in got[0][2]["note"]


def test_law_mention_requires_same_ministry():
    docs, cache = _council(1, ministry="法務省")
    assert match_refs.law_mention_candidates(BILL, _index(docs, cache), {}) == []


def test_plain_mentions_without_amend_context_are_not_used():
    docs, cache = _council(1)
    for entry in cache.values():
        entry["amend"] = {}
    assert match_refs.law_mention_candidates(BILL, _index(docs, cache), {}) == []


def test_law_mentioned_by_many_councils_is_too_generic():
    docs, cache = [], {}
    for n in range(match_refs.MAX_LAW_COUNCILS + 1):
        d, c = _council(n)
        docs += d
        cache.update(c)
    assert match_refs.law_mention_candidates(BILL, _index(docs, cache), {}) == []


def test_law_mention_respects_meeting_time_window():
    docs, cache = _council(1)
    mcache = {"https://npa.test/1.html": "2020-01"}      # 提出の3年以上前
    assert match_refs.law_mention_candidates(BILL, _index(docs, cache), mcache) == []


def test_page_months_reads_reiwa_with_fullwidth_digits():
    months = match_refs.page_months("第５回（令和８年９月24日） 2025年3月 令和元年５月")
    assert months == {"2026-09", "2025-03", "2019-05"}


def test_gate_prompt_includes_note():
    prompt = llm_gate._prompt({"title": "法案", "summary": ""},
                              {"title": "検討会", "note": "「X法」の改正・規定に2回言及"})
    assert "参考文書の補足: 「X法」の改正・規定に2回言及" in prompt
