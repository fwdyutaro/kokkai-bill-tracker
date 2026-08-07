# -*- coding: utf-8 -*-
"""
情報提供（参考URL）の取り込み・関連度推定
GitHub Issue で投稿された (対象法案 + URL) を処理する中核ロジック。

- URLから書誌メタ（タイトル・概要・発行元）を抽出（og/citation/JSON-LD/title）。
- 既存のマッチャ(match_refs)を流用して法案との関連度を推定。
- 承認用のMarkdownコメントと、掲載用のJSONレコードを生成。

CLI（ローカル/Action双方で利用）:
  python ingest_submission.py --bill "閣法 第31号" --url "https://..."
  python ingest_submission.py --issue-body "$BODY" --issue-title "$TITLE"
"""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
import match_refs as M

UA = {"User-Agent": "Mozilla/5.0 (compatible; bill-tracker/0.1; research)"}
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 10
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
EXIT_MANUAL_REVIEW = 1
EXIT_INVALID_INPUT = 2
EXIT_FAILED = 3
HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
BILL_NO_RE = re.compile(r"(閣法|衆法|参法)\s*第?\s*(\d+)\s*号")
BILL_ID_RE = re.compile(r"\b(\d+-(?:閣法|衆法|参法)-?\d+)\b")
URL_RE = re.compile(r"https?://[^\s)>\]」]+")
GENERIC_TITLES = ("国立国会図書館デジタルコレクション", "CiNii Research", "お探しのページ")
_DNS_PIN_LOCK = threading.Lock()


class SubmissionFetchError(RuntimeError):
    """利用者向けURLを安全に取得できない場合の、機密情報を含まない例外。"""


class UnsafeURLError(SubmissionFetchError):
    """URLまたは名前解決結果が公開Webアクセスとして安全でない。"""


@dataclass(frozen=True)
class ValidatedURL:
    url: str
    hostname: str
    address_info: tuple


@dataclass(frozen=True)
class FetchedHTML:
    content: bytes
    url: str


def _normalise_hostname(hostname):
    """比較とDNS固定に使うASCIIホスト名へ正規化する。"""
    hostname = hostname.rstrip(".").lower()
    if not hostname or "%" in hostname:
        raise UnsafeURLError("ホスト名が不正です")
    try:
        return hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeURLError("ホスト名が不正です") from exc


def _allowed_domains():
    raw = os.environ.get("SUBMISSION_ALLOWED_DOMAINS", "")
    result = []
    for item in raw.split(","):
        item = item.strip()
        if item:
            result.append(_normalise_hostname(item))
    return tuple(result)


def _is_public_address(address):
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_unspecified
    )


def validate_public_url(url):
    """URLを検査してDNS結果を返す。実際の接続ではこの結果を固定して使う。"""
    if not isinstance(url, str) or not url or any(ord(c) <= 32 for c in url):
        raise UnsafeURLError("URLの形式が不正です")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURLError("URLの形式が不正です") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeURLError("http/https以外のURLは利用できません")
    if not parsed.hostname:
        raise UnsafeURLError("ホスト名がありません")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeURLError("認証情報を含むURLは利用できません")
    if port is not None and port not in {80, 443}:
        raise UnsafeURLError("許可されていないポートです")

    hostname = _normalise_hostname(parsed.hostname)
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise UnsafeURLError("ローカルホストは利用できません")

    allowlist = _allowed_domains()
    if allowlist and not any(hostname == d or hostname.endswith("." + d) for d in allowlist):
        raise UnsafeURLError("許可されていないドメインです")

    lookup_port = port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        info = tuple(socket.getaddrinfo(hostname, lookup_port, 0, socket.SOCK_STREAM))
    except socket.gaierror as exc:
        raise UnsafeURLError("ホスト名を解決できません") from exc
    if not info:
        raise UnsafeURLError("ホスト名を解決できません")
    if any(not _is_public_address(item[4][0]) for item in info):
        raise UnsafeURLError("公開インターネット以外の接続先は利用できません")
    return ValidatedURL(url=url, hostname=hostname, address_info=info)


@contextmanager
def _pinned_dns(validated):
    """検査済みDNS結果をrequests/urllib3の接続中だけ固定する。

    socket.getaddrinfo はプロセス全体の関数なので、ロックにより同一プロセス内の
    HTTP取得を直列化する。ingest_submission.py は単一スレッドのCLIとして使う。
    """
    with _DNS_PIN_LOCK:
        original = socket.getaddrinfo

        def pinned(host, port, family=0, socktype=0, proto=0, flags=0):
            comparable = host.decode("ascii") if isinstance(host, bytes) else str(host)
            try:
                comparable = _normalise_hostname(comparable)
            except UnsafeURLError:
                comparable = ""
            if comparable != validated.hostname:
                return original(host, port, family, socktype, proto, flags)
            matches = []
            for af, st, pr, canon, sockaddr in validated.address_info:
                if family not in (0, af) or socktype not in (0, st) or proto not in (0, pr):
                    continue
                # 接続先ポートは呼出側の値を尊重し、IPだけを検査時の値に固定する。
                target = (sockaddr[0], port, *sockaddr[2:])
                matches.append((af, st, pr, canon, target))
            if not matches:
                raise socket.gaierror(socket.EAI_NONAME, "pinned DNS result unavailable")
            return matches

        socket.getaddrinfo = pinned
        try:
            yield
        finally:
            socket.getaddrinfo = original


def fetch_public_html(url, _session=None):
    """公開HTMLだけを、各リダイレクト先の再検査とサイズ制限付きで取得する。"""
    session = _session or requests.Session()
    owns_session = _session is None
    session.trust_env = False  # 環境proxy・認証情報を投稿先へ引き継がない
    current = url
    try:
        for redirect_count in range(MAX_REDIRECTS + 1):
            validated = validate_public_url(current)
            session.cookies.clear()
            try:
                with _pinned_dns(validated):
                    response = session.get(
                        current,
                        allow_redirects=False,
                        stream=True,
                        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                        headers=UA,
                        verify=True,
                    )
            except requests.RequestException as exc:
                raise SubmissionFetchError("URLへの接続または読み取りに失敗しました") from exc

            try:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        raise SubmissionFetchError("リダイレクト先がありません")
                    if redirect_count >= MAX_REDIRECTS:
                        raise SubmissionFetchError("リダイレクト回数が上限を超えました")
                    current = urljoin(current, location)
                    continue
                if response.status_code != 200:
                    raise SubmissionFetchError("HTTP 200以外の応答でした")

                media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if media_type not in HTML_CONTENT_TYPES:
                    raise SubmissionFetchError("HTML/XHTML以外の応答は利用できません")
                length = response.headers.get("Content-Length")
                try:
                    if length is not None and int(length) > MAX_HTML_BYTES:
                        raise SubmissionFetchError("応答サイズが2 MiBを超えています")
                except ValueError:
                    pass

                chunks = []
                size = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > MAX_HTML_BYTES:
                        raise SubmissionFetchError("応答サイズが2 MiBを超えています")
                    chunks.append(chunk)
                return FetchedHTML(content=b"".join(chunks), url=current)
            except requests.RequestException as exc:
                raise SubmissionFetchError("URLへの接続または読み取りに失敗しました") from exc
            finally:
                response.close()
    finally:
        if owns_session:
            session.close()

    raise SubmissionFetchError("HTMLを取得できませんでした")


def extract_meta(url):
    """URLからタイトル・概要・発行元を抽出。JS描画等で取れない場合は title=None。"""
    fetched = fetch_public_html(url)
    soup = BeautifulSoup(fetched.content, "html.parser")

    def meta(*names):
        for n in names:
            t = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if t and t.get("content"):
                return t["content"].strip()
        return None

    title = meta("og:title", "citation_title", "dc.title")
    if not title:
        for ld in soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(ld.string)
                d = d[0] if isinstance(d, list) else d
                title = d.get("name") or d.get("headline")
                if title:
                    break
            except Exception:
                pass
    if not title and soup.title:
        title = soup.title.get_text(strip=True)
    # サイト名接尾辞を除去（「… | CiNii Research」「… - NHK」等）
    if title:
        title = re.split(r"\s*[|｜\-—–]\s*[^|｜\-—–]{2,30}$", title)[0].strip()
    publisher = meta("og:site_name", "citation_journal_title") or urlsplit(fetched.url).hostname
    desc = meta("og:description", "description", "citation_abstract") or ""
    # 取得できても汎用タイトルなら手動補完が必要
    manual = (not title) or any(g in title for g in GENERIC_TITLES)
    return {"title": title, "desc": desc[:200], "publisher": publisher,
            "status": 200, "manual": manual}


def estimate_relevance(bill, title, desc=""):
    """match_refs の語彙＋趣旨キーワードで関連度(%)と根拠を推定。"""
    if not title:
        return 0, "タイトル取得不可（手動確認）"
    sc, why = M.score(bill["title"], title)
    if sc < M.ATTACH_THRESHOLD:
        pkw = M.keywords_from_purpose(bill.get("summary"))
        blob = M.norm(title + desc)
        hit = next((k for k in sorted(pkw, key=len, reverse=True) if k in blob), None)
        if hit:
            sc, why = min(0.8, 0.58 + len(hit) / 30), f"趣旨キーワード一致（{hit}）"
    return round(sc * 100), why


def _normalise_bill_no(value):
    match = BILL_NO_RE.search(str(value or ""))
    return (match.group(1), int(match.group(2))) if match else None


def _bill_candidates(bills, bill_no_tuple):
    if not bill_no_tuple:
        return []
    wanted = (bill_no_tuple[0], int(bill_no_tuple[1]))
    return [b for b in bills if _normalise_bill_no(b.get("no")) == wanted]


def find_bill(bills, bill_no_tuple=None, bill_id=None):
    if bill_id:
        bill = next((b for b in bills if b.get("id") == bill_id), None)
        if bill is not None and bill_no_tuple and _normalise_bill_no(bill.get("no")) != (bill_no_tuple[0], int(bill_no_tuple[1])):
            return None
        return bill
    candidates = _bill_candidates(bills, bill_no_tuple)
    return candidates[0] if len(candidates) == 1 else None


def process(bill_no_tuple, url, bills, bill_id=None):
    bill = find_bill(bills, bill_no_tuple, bill_id=bill_id)
    if not bill:
        return None, f"対象法案が見つかりません: {bill_id or bill_no_tuple}"
    if bill_id and bill_no_tuple and _normalise_bill_no(bill.get("no")) != (bill_no_tuple[0], int(bill_no_tuple[1])):
        return None, "bill_id と議案番号が一致しません"
    meta = extract_meta(url)
    pct, why = estimate_relevance(bill, meta["title"], meta["desc"])
    rec = {"bill_id": bill.get("id"), "bill_no": bill["no"], "title": meta["title"] or "(要手動補完)",
           "url": url, "publisher": meta["publisher"], "relevance": pct, "why": why,
           "manual": meta["manual"], "status": "pending"}
    md = (f"### 自動処理結果\n"
          f"- **対象法案**: {bill['no']} {bill['shortTitle']}\n"
          f"- **URL**: {url}\n"
          f"- **抽出タイトル**: {meta['title'] or '（取得できず・手動補完が必要）'}\n"
          f"- **発行元**: {meta['publisher']}\n"
          f"- **推定関連度**: {pct}%（{why}）\n\n"
          + ("⚠ タイトルが自動取得できませんでした。コメントで正しいタイトルを補ってください。\n"
             if meta["manual"] else "")
          + "問題なければメンテナが `approved` ラベルを付与すると掲載されます。")
    return rec, md


def parse_issue_details(title, body):
    text = f"{title}\n{body or ''}"
    bm = BILL_NO_RE.search(text)
    im = BILL_ID_RE.search(text)
    um = URL_RE.search(body or "")
    bnt = (bm.group(1), bm.group(2)) if bm else None
    return (im.group(1) if im else None, bnt, um.group(0) if um else None)


def parse_issue(title, body):
    """旧API。bill_idなしのIssueは従来どおり (議案番号, URL) を返す。"""
    details = parse_issue_details(title, body)
    if details[0]:
        return details
    return details[1], details[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bill"); ap.add_argument("--url")
    ap.add_argument("--bill-id")
    ap.add_argument("--issue-title", default=""); ap.add_argument("--issue-body", default="")
    ap.add_argument("--out", help="JSONレコードの出力先")
    args = ap.parse_args()

    if args.out:
        try:
            Path(args.out).unlink(missing_ok=True)
        except OSError as exc:
            print(f"既存の掲載レコードを整理できませんでした: {type(exc).__name__}", file=sys.stderr)
            return EXIT_FAILED
    if args.bill and args.url:
        bn = BILL_NO_RE.search(args.bill); bnt = (bn.group(1), bn.group(2)) if bn else None
        im = BILL_ID_RE.search(args.bill); inferred_id = im.group(1) if im else None
        if args.bill_id and inferred_id and args.bill_id != inferred_id:
            print("bill_id と議案番号の指定が一致しません。", file=sys.stderr)
            return EXIT_MANUAL_REVIEW
        bill_id = args.bill_id or inferred_id
        url = args.url
    elif args.bill_id and args.url:
        bill_id, bnt, url = args.bill_id, None, args.url
    else:
        bill_id, bnt, url = parse_issue_details(args.issue_title, args.issue_body)
        if args.bill_id:
            inferred_id = bill_id
            if inferred_id and inferred_id != args.bill_id:
                print("bill_id の指定がIssue本文と一致しません。", file=sys.stderr)
                return EXIT_MANUAL_REVIEW
            bill_id = args.bill_id
    if (not bill_id and not bnt) or not url:
        print("法案番号またはURLを解釈できませんでした。", file=sys.stderr)
        print("対象法案とURLを確認してください。")
        return EXIT_INVALID_INPUT

    try:
        bills = json.load(open("bills.json", encoding="utf-8"))
        rec, md = (process(bnt, url, bills, bill_id=bill_id)
                   if bill_id else process(bnt, url, bills))
    except SubmissionFetchError as exc:
        print(f"安全にURLを取得できませんでした: {exc}", file=sys.stderr)
        print("URLを安全に取得できなかったため、手動確認が必要です。")
        return EXIT_MANUAL_REVIEW
    except Exception as exc:
        # URLやrequests例外本文をログへ出さず、予期しない障害として区別する。
        print(f"自動処理で予期しないエラーが発生しました: {type(exc).__name__}", file=sys.stderr)
        return EXIT_FAILED
    print(md)
    if not rec:
        return EXIT_INVALID_INPUT
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as target:
                json.dump(rec, target, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f"掲載レコードを保存できませんでした: {type(exc).__name__}", file=sys.stderr)
            return EXIT_FAILED
    return 0


if __name__ == "__main__":
    sys.exit(main())
