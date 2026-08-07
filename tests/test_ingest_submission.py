import json
import socket
import sys
from urllib.parse import urlsplit

import pytest

import ingest_submission as ingest


def addrinfo(ip, port=443):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)


def install_resolver(monkeypatch, mapping=None, default="93.184.216.34"):
    mapping = mapping or {}
    calls = []

    def resolve(host, port, family=0, socktype=0, proto=0, flags=0):
        calls.append((host, port))
        values = mapping.get(str(host).rstrip(".").lower(), [default])
        return [addrinfo(ip, port) for ip in values]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    return calls


class FakeCookies:
    def __init__(self):
        self.cleared = 0

    def clear(self):
        self.cleared += 1


class FakeResponse:
    def __init__(self, status=200, headers=None, chunks=None):
        self.status_code = status
        self.headers = headers or {}
        self.chunks = chunks or []
        self.closed = False

    def iter_content(self, chunk_size):
        yield from self.chunks

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.cookies = FakeCookies()
        self.calls = []
        self.trust_env = True

    def get(self, url, **kwargs):
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        # 実接続と同様、検証後にもう一度名前解決する。ここでは固定結果になるはず。
        resolved = socket.getaddrinfo(parsed.hostname, port, 0, socket.SOCK_STREAM)
        self.calls.append((url, kwargs, resolved))
        return self.responses.pop(0)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "https://user@example.com/",
        "https://user:secret@example.com/",
        "https://example.com:8080/",
        "https://localhost/",
        "https://sub.localhost/",
        "https://example.com/a\nb",
    ],
)
def test_validate_rejects_invalid_authority_without_network(monkeypatch, url):
    calls = install_resolver(monkeypatch)
    with pytest.raises(ingest.UnsafeURLError):
        ingest.validate_public_url(url)
    assert calls == []


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
    ],
)
def test_validate_rejects_non_global_addresses(monkeypatch, ip):
    install_resolver(monkeypatch, {"example.com": [ip]})
    with pytest.raises(ingest.UnsafeURLError, match="公開インターネット以外"):
        ingest.validate_public_url("https://example.com/")


def test_validate_rejects_mixed_public_and_private_dns(monkeypatch):
    install_resolver(monkeypatch, {"example.com": ["93.184.216.34", "10.0.0.1"]})
    with pytest.raises(ingest.UnsafeURLError):
        ingest.validate_public_url("https://example.com/")


def test_allowlist_accepts_domain_and_subdomain_only(monkeypatch):
    install_resolver(monkeypatch)
    monkeypatch.setenv("SUBMISSION_ALLOWED_DOMAINS", "example.com")
    ingest.validate_public_url("https://docs.example.com/")
    with pytest.raises(ingest.UnsafeURLError, match="ドメイン"):
        ingest.validate_public_url("https://notexample.com/")


def test_fetch_pins_validated_dns_and_sets_transport_limits(monkeypatch):
    resolver_calls = install_resolver(monkeypatch)
    response = FakeResponse(
        headers={"Content-Type": "text/html; charset=utf-8"},
        chunks=[b"<title>ok</title>"],
    )
    session = FakeSession([response])

    fetched = ingest.fetch_public_html("https://example.com/page", _session=session)

    assert fetched.content == b"<title>ok</title>"
    assert len(resolver_calls) == 1  # 接続時の2回目は固定結果を使い、再解決しない
    _, kwargs, resolved = session.calls[0]
    assert resolved[0][4][0] == "93.184.216.34"
    assert kwargs["timeout"] == (5, 10)
    assert kwargs["allow_redirects"] is False
    assert kwargs["stream"] is True
    assert kwargs["verify"] is True
    assert session.trust_env is False
    assert session.cookies.cleared == 1
    assert response.closed


def test_fetch_revalidates_redirect_and_blocks_private_target(monkeypatch):
    install_resolver(
        monkeypatch,
        {"public.example": ["93.184.216.34"], "internal.example": ["10.0.0.2"]},
    )
    redirect = FakeResponse(302, {"Location": "http://internal.example/admin"})
    session = FakeSession([redirect])

    with pytest.raises(ingest.UnsafeURLError):
        ingest.fetch_public_html("https://public.example/start", _session=session)

    assert len(session.calls) == 1
    assert redirect.closed


def test_fetch_rejects_more_than_three_redirects(monkeypatch):
    install_resolver(monkeypatch)
    responses = [FakeResponse(302, {"Location": f"/hop/{n}"}) for n in range(4)]
    session = FakeSession(responses)
    with pytest.raises(ingest.SubmissionFetchError, match="リダイレクト回数"):
        ingest.fetch_public_html("https://example.com/start", _session=session)
    assert len(session.calls) == 4


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (FakeResponse(404, {"Content-Type": "text/html"}), "HTTP 200"),
        (FakeResponse(200, {"Content-Type": "application/pdf"}), "HTML/XHTML"),
        (
            FakeResponse(
                200,
                {"Content-Type": "text/html", "Content-Length": str(ingest.MAX_HTML_BYTES + 1)},
            ),
            "2 MiB",
        ),
    ],
)
def test_fetch_rejects_bad_response_metadata(monkeypatch, response, message):
    install_resolver(monkeypatch)
    with pytest.raises(ingest.SubmissionFetchError, match=message):
        ingest.fetch_public_html("https://example.com/", _session=FakeSession([response]))
    assert response.closed


def test_fetch_stops_when_stream_exceeds_two_mib(monkeypatch):
    install_resolver(monkeypatch)
    response = FakeResponse(
        200,
        {"Content-Type": "application/xhtml+xml"},
        [b"a" * ingest.MAX_HTML_BYTES, b"b"],
    )
    with pytest.raises(ingest.SubmissionFetchError, match="2 MiB"):
        ingest.fetch_public_html("https://example.com/", _session=FakeSession([response]))
    assert response.closed


def test_main_fetch_failure_returns_one_and_does_not_write_record(monkeypatch, tmp_path):
    (tmp_path / "bills.json").write_text(json.dumps([]), encoding="utf-8")
    output = tmp_path / "rec.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        ingest,
        "process",
        lambda *args: (_ for _ in ()).throw(ingest.SubmissionFetchError("取得失敗")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["ingest_submission.py", "--bill", "閣法 第1号", "--url", "https://example.com", "--out", str(output)],
    )

    assert ingest.main() == 1
    assert not output.exists()


def test_main_unexpected_failure_returns_three(monkeypatch, tmp_path):
    (tmp_path / "bills.json").write_text(json.dumps([]), encoding="utf-8")
    output = tmp_path / "rec.json"
    output.write_text("stale", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        ingest, "process", lambda *args: (_ for _ in ()).throw(RuntimeError("unexpected"))
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["ingest_submission.py", "--bill", "閣法 第1号", "--url", "https://example.com", "--out", str(output)],
    )

    assert ingest.main() == 3
    assert not output.exists()


def test_find_bill_legacy_does_not_confuse_number_prefix(tmp_path):
    bills = [
        {"id": "221-閣法-10", "no": "閣法 第10号"},
        {"id": "222-閣法-1", "no": "閣法 第1号"},
    ]
    assert ingest.find_bill(bills, ("閣法", "1"))["id"] == "222-閣法-1"


def test_process_bill_id_and_bill_no_mismatch_is_manual(monkeypatch):
    bill = {"id": "222-閣法-10", "no": "閣法 第10号", "title": "x", "summary": ""}
    rec, _ = ingest.process(("閣法", "1"), "https://example.com", [bill], bill_id=bill["id"])
    assert rec is None


def test_parse_issue_extracts_bill_id_and_url_with_closing_quote():
    parsed = ingest.parse_issue("", "bill_id: 222-閣法-1\n閣法 第1号\nhttps://example.com/ref」")
    assert parsed[0] == "222-閣法-1"
    assert parsed[2] == "https://example.com/ref"


def test_main_accepts_explicit_bill_id(monkeypatch, tmp_path):
    (tmp_path / "bills.json").write_text("[]", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    seen = {}
    monkeypatch.setattr(ingest, "process", lambda bnt, url, bills, bill_id=None: (seen.update({"id": bill_id}) or ({"bill_id": bill_id}, "ok")))
    monkeypatch.setattr(sys, "argv", ["ingest_submission.py", "--bill-id", "221-閣法-1", "--url", "https://example.com"])
    assert ingest.main() == 0
    assert seen["id"] == "221-閣法-1"
