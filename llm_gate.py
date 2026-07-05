# -*- coding: utf-8 -*-
"""
LLM関連判定ゲート
意味類似・趣旨キーワード等の「弱い候補」を、LLMで(法案×参考文書)の関連性を
yes/no＋一文根拠で検証し、無関係を除去・関連は根拠を付与する。

バックエンドは ローカルLLM(Ollama) を優先し、無ければ Anthropic API、
どちらも無ければ何もしない（候補をそのまま返す）。
判定は gate_cache.json にキャッシュし、日次実行での再判定を避ける。
"""
import hashlib, json, os

import requests

# 検証対象とする紐付け根拠（審議会の「所管一致＋関連語」も、緩い趣旨KW由来の
# 混入（例: 競争環境→モバイル市場研究会）があるため対象に含める）
SOFT = ("意味類似", "趣旨キーワード", "語彙類似", "所管一致")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:12b")
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
CACHE = "gate_cache.json"

_SYSTEM = ("あなたは立法情報の編集者です。参考文書がその法案の審議の参考として"
           "関連するかを厳しめに判定し、JSONのみ返す: "
           '{"related": true/false, "reason": "一文"}')


def _backend():
    """利用可能なバックエンド名を返す（毎回のプローブは軽いGET1回）。"""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        if r.status_code == 200:
            return "ollama"
    except Exception:
        pass
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def _prompt(bill, doc):
    ab = (doc.get("abstract") or "").strip()
    return (f"法案名: {bill['title']}\n"
            f"法案の趣旨: {(bill.get('summary') or '')[:300]}\n"
            f"参考文書タイトル: {doc['title']}\n"
            + (f"参考文書の冒頭: {ab[:300]}\n" if ab else "")
            + '\nJSONのみで答えてください: {"related": true/false, "reason": "一文"}')


def _parse(txt):
    try:
        return json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
    except Exception:
        return {"related": True, "reason": ""}   # 解析失敗時は残す（安全側）


def _judge_ollama(bill, doc):
    r = requests.post(f"{OLLAMA_URL}/api/generate",
                      json={"model": OLLAMA_MODEL,
                            "prompt": _SYSTEM + "\n\n" + _prompt(bill, doc),
                            "stream": False, "format": "json",
                            "options": {"temperature": 0.1, "num_predict": 150}},
                      timeout=180)
    r.raise_for_status()
    return _parse(r.json().get("response") or "")


def _judge_anthropic(bill, doc):
    import anthropic
    msg = anthropic.Anthropic().messages.create(
        model=ANTHROPIC_MODEL, max_tokens=150, system=_SYSTEM,
        messages=[{"role": "user", "content": _prompt(bill, doc)}])
    return _parse(msg.content[0].text.strip())


def _load_cache():
    if os.path.exists(CACHE):
        try:
            return json.load(open(CACHE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


class Gate:
    def __init__(self):
        self.backend = _backend()
        self.cache = _load_cache()
        self.dirty = False
        self.stats = {"pass": 0, "drop": 0, "cached": 0}

    def available(self):
        return self.backend is not None

    def _key(self, bill, doc):
        return hashlib.sha1(f"{bill['no']}|{doc['url']}".encode()).hexdigest()[:16]

    def judge(self, bill, doc):
        key = self._key(bill, doc)
        if key in self.cache:
            self.stats["cached"] += 1
            return self.cache[key]
        try:
            v = _judge_ollama(bill, doc) if self.backend == "ollama" else _judge_anthropic(bill, doc)
        except Exception as e:
            v = {"related": True, "reason": f"判定失敗のため保持({str(e)[:30]})"}
        self.cache[key] = v
        self.dirty = True
        return v

    def filter(self, bill, cands, verbose=False):
        """cands=[(score, why, doc)] のうち弱い根拠のものをLLMで検証して絞る。"""
        out = []
        for sc, why, d in cands:
            if not any(s in why for s in SOFT):     # 強い根拠はそのまま採用
                out.append((sc, why, d))
                continue
            v = self.judge(bill, d)
            if v.get("related"):
                reason = (v.get("reason") or "").strip()
                out.append((sc, why + (f"／LLM確認: {reason}" if reason else "／LLM確認済"), d))
                self.stats["pass"] += 1
            else:
                self.stats["drop"] += 1
                if verbose:
                    print(f"    × LLM除外: {d['title'][:34]} 〈{(v.get('reason') or '')[:40]}〉")
        return out

    def save(self):
        if self.dirty:
            json.dump(self.cache, open(CACHE, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)


# 旧インターフェース互換（match_refs 旧版から呼ばれても動くように）
def gate(bill, cands, verbose=False):
    g = Gate()
    if not g.available():
        return cands
    out = g.filter(bill, cands, verbose)
    g.save()
    return out
