# -*- coding: utf-8 -*-
"""
LLM関連判定ゲート（任意）
意味類似・趣旨キーワード等の「弱い候補」を、LLMで(法案×参考文書)の関連性を
yes/no＋一文根拠で検証し、無関係を除去・関連は根拠を付与する。

- 要 ANTHROPIC_API_KEY。無ければ何もしない（候補をそのまま返す）。
- 確実な根拠で付いた候補（法案名一致・所管一致＋通称）はゲート対象外（コスト削減）。
- モデルは安価・高速な Haiku を既定に。
"""
import json, os

SOFT = ("意味類似", "趣旨キーワード", "語彙類似")   # 検証対象とする紐付け根拠
MODEL = "claude-haiku-4-5-20251001"


def _available():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        return anthropic.Anthropic()
    except Exception:
        return None


def _judge(client, bill, doc_title):
    prompt = (f"法案名: {bill['title']}\n"
              f"法案の趣旨: {(bill.get('summary') or '')[:300]}\n"
              f"参考文書のタイトル: {doc_title}\n\n"
              "この参考文書は、上記法案の審議の参考情報として関連していますか。"
              'JSONのみで答えてください: {"related": true/false, "reason": "一文"}')
    msg = client.messages.create(
        model=MODEL, max_tokens=120,
        system="あなたは立法情報の編集者です。関連性を厳しめに判定し、JSONのみ返す。",
        messages=[{"role": "user", "content": prompt}])
    txt = msg.content[0].text.strip()
    try:
        return json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
    except Exception:
        return {"related": True, "reason": ""}   # 解析失敗時は残す


def gate(bill, cands, verbose=False):
    """cands=[(score, why, doc)] を関連性で絞る。client無しなら素通し。"""
    client = _available()
    if not client:
        return cands
    out = []
    for sc, why, d in cands:
        if not any(s in why for s in SOFT):     # 強い根拠はそのまま採用
            out.append((sc, why, d))
            continue
        v = _judge(client, bill, d["title"])
        if v.get("related"):
            reason = v.get("reason", "")
            out.append((sc, why + (f"／LLM確認: {reason}" if reason else "／LLM確認"), d))
        elif verbose:
            print(f"    × LLM除外: {d['title'][:30]} （{v.get('reason','')[:30]}）")
    return out
