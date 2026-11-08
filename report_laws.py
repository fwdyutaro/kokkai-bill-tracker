# -*- coding: utf-8 -*-
"""
審議会・検討会の報告書／提言PDFが言及している法令名のインデックス。

改正法案の前に開かれた審議会・検討会の報告書は「〇〇法の中に規定することが
考えられる」のように改正先の法令名を書くことが多い。件名や趣旨の語が会議名と
重ならなくても（例: 警察官職務執行法 ↔ 匿名・流動型犯罪グループ…有識者検討会）、
この言及を手がかりに紐付けられる。

本文は保存しない（crawl.py と同じ著作権・規約配慮）。PDFから取り出すのは
「どの法令名に何回言及しているか」だけで、それを report_laws.json に保存する。
  laws  … 単なる言及の回数（参考情報）
  amend … 「〇〇法を改正」「〇〇法の中に規定」など、改正・規定の文脈での言及の回数。
          紐付けにはこちらを使う（法令名に触れただけの報告書を拾わないため）。
報告書は「〇〇に関する法律（以下「〇〇法」という。）」と略称を定めて以後は略称で
書くことが多いので、略称での言及は正式名称の分として数える。
refs.db はリポジトリに含めず CI で毎回作り直すため、抽出結果をこのファイルで
共有し、CI（PDFを読み直さない）でも週次処理と同じ照合ができるようにしている。

  crawl.py      … 新しい報告書PDFだけを取得して抽出し、report_laws.json に追記
  match_refs.py … report_laws.json を読み、件名の改正対象法令と照合
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter
from datetime import date

CACHE = "report_laws.json"

# 2字の法令名（〇〇法 の一般則から外れるため個別に許可）
SHORT_LAWS = {"民法", "刑法", "商法", "憲法"}
# 「法」で終わるが法令名ではない語（末尾2字で判定）
NON_LAW_SUFFIX = {
    "方法", "手法", "用法", "違法", "適法", "合法", "不法", "司法", "立法", "寸法",
    "製法", "工法", "技法", "話法", "作法", "文法", "療法", "便法", "解法", "算法",
    "論法", "兵法", "魔法", "現法", "本法", "同法", "新法", "旧法", "各法", "諸法",
    "他法", "両法", "前法", "後法", "母法",
}
# 法令名を構成しうる文字（漢字・カタカナ・中点）と「及び」「並びに」
_LAW_CHARS = r"(?:[一-龠々〆ヶァ-ヴー・]|及び|並びに)"
# 「〇〇法」: 直後が 法人/法律/法令/法案 等なら法令名ではない
LAW_RE = re.compile(_LAW_CHARS + r"{1,40}?法(?![人律令案的務廷曹規制典学益則定理])")
# 「〇〇に関する法律」等: 直前の区切り（句読点・括弧）から取り出す
# 目次の点線・ページ番号（.....17 等）を拾わないよう、算用数字と点・ダッシュも区切りとして扱う
LAW_LONG_RE = re.compile(
    r"[^、。，．「」『』（）()［］\[\]【】〈〉0-9０-９.…‥\-－―]{1,80}?(?:に関する|のための)?法律(?![案上])")


# 法令名の直後がこれなら「改正・規定の文脈」（既存規定の引用「〇〇法の規定に基づき」は含めない）
AMEND_AFTER = re.compile(
    r"^(?:（[^）]{0,40}）)?(?:第[^をのに]{0,12}条[^をのに]{0,8})?"
    r"(?:(?:を|の)(?:一部(?:を)?)?改正|改正(?:する|し|案|法|により|による|後)"
    r"|(?:の中)?に(?:新たに)?(?:規定(?:す|し|を)|位置付け|位置づけ|盛り込|追加|明記|設け|根拠を)"
    r"|上の(?:権限|措置|規定)として)")
# 略称の定義「〇〇法（以下「略称」という。）」
ABBR_RE = re.compile(r"^[（(][^）)]{0,30}?以下[、]?「([^」]{2,25})」")


def _flat(text):
    return re.sub(r"\s+", "", text or "")


def _law_tokens(flat):
    """(法令名, 開始位置, 終了位置) を本文の出現順に返す。"""
    found = []
    for m in LAW_RE.finditer(flat):
        tok = m.group(0)
        # 「及び」で始まる断片や、法令名でない「〜法」を除く
        lead = re.match(r"^(?:及び|並びに)+", tok)
        start = m.start() + (lead.end() if lead else 0)
        tok = tok[lead.end():] if lead else tok
        if len(tok) < 2 or tok[-2:] in NON_LAW_SUFFIX:
            continue
        if len(tok) == 2 and tok not in SHORT_LAWS:
            continue
        found.append((tok, start, m.end()))
    for m in LAW_LONG_RE.finditer(flat):
        tok = m.group(0)
        # 「この法律」等の一般的な言い回しは除き、題名の形をしたものだけ数える
        if "に関する法律" in tok or "のための法律" in tok:
            found.append((tok, m.start(), m.end()))
    return sorted(found, key=lambda x: x[1])


def law_mentions(text):
    """本文から法令名への言及を数える。{"laws": {名: 回数}, "amend": {名: 回数}}。"""
    flat = _flat(text)
    tokens = _law_tokens(flat)
    # 略称 → 正式名称
    abbr = {}
    for tok, _, end in tokens:
        m = ABBR_RE.match(flat[end:end + 40])
        if m and m.group(1) != tok:
            abbr[m.group(1)] = tok
    laws, amend = Counter(), Counter()
    for tok, _, end in tokens:
        name = abbr.get(tok, tok)
        laws[name] += 1
        if AMEND_AFTER.match(flat[end:end + 40]):
            amend[name] += 1
    return {"laws": dict(laws), "amend": dict(amend)}


# 件名 → 改正対象の法令名
_AMEND_RE = re.compile(r"^(?P<laws>.+?)(?:等)?の一部を改正する(?:等の)?法律案$")
# 「A法及びB法」の区切り（直前が「法」「法律」のときだけ。外国為替及び外国貿易法 等を割らない）
_SPLIT_RE = re.compile(r"(?<=[法律])(?:及び|並びに|、)")


def amended_laws(title):
    """件名から改正対象（または制定対象）の法令名を返す。

    「警察官職務執行法の一部を改正する法律案」→ ["警察官職務執行法"]
    「A法及びB法の一部を改正する法律案」      → ["A法", "B法"]
    「〇〇に関する法律案」（新法）            → ["〇〇に関する法律"]
    """
    title = _flat(title)
    m = _AMEND_RE.match(title)
    if m:
        laws = [x for x in _SPLIT_RE.split(m.group("laws")) if x]
    elif title.endswith("法律案"):
        laws = [title[:-1]]
    else:
        return []
    out = []
    for law in laws:
        law = re.sub(r"等$", "", law)
        if len(law) >= 3 or law in SHORT_LAWS:
            out.append(law)
    return out


def mentions_law(mentions, law):
    """{法令名: 回数}（law_mentions の laws / amend）のうち law への言及の回数。"""
    n = 0
    for tok, c in (mentions or {}).items():
        if tok == law or (len(law) >= 4 and tok.endswith(law)):
            n += c
    return n


# ---- キャッシュ（report_laws.json） -----------------------------------------

def load_cache(path=CACHE):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cache(cache, path=CACHE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(cache.items())), f, ensure_ascii=False, indent=1)
        f.write("\n")


def pdf_text(pdf_bytes, max_pages=80):
    from io import BytesIO
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(pdf_bytes))
    return " ".join((p.extract_text() or "") for p in reader.pages[:max_pages])


def update_cache(con, fetch, cache, sleep=0.6, today=None):
    """refs.db の報告書PDF（審議会・検討会のPDF）のうち未抽出のものを処理する。

    fetch(url) は PDF の bytes を返す関数。失敗は laws={} ・ok=False で記録し、
    毎回の再取得を避ける（再試行したい場合はエントリを消す）。
    """
    rows = con.execute(
        """SELECT url FROM documents
           WHERE category='審議会・検討会' AND lower(url) LIKE '%.pdf'""").fetchall()
    todo = [u for (u,) in rows if u not in cache]
    stamp = (today or date.today()).isoformat()
    ok = 0
    for url in todo:
        entry = {"laws": {}, "amend": {}, "ok": False, "checked": stamp}
        try:
            data = fetch(url)
            if data[:4] == b"%PDF":
                entry.update(law_mentions(pdf_text(data)), ok=True)
                ok += 1
        except Exception as e:
            print(f"    ! 報告書PDFの取得失敗 {url}: {str(e)[:60]}", file=sys.stderr)
        cache[url] = entry
        time.sleep(sleep)
    print(f"  [report_laws] 新規{len(todo)}件 抽出成功{ok}件（累計{len(cache)}件）", file=sys.stderr)
    return len(todo)
