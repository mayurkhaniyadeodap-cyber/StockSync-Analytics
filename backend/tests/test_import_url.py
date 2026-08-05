"""Fetching a CSV from a URL — the download, its guards, and its failures.

The network is replaced at the transport, not at ``httpx.get``: the real client
still parses the response, decides what is a redirect and drives the stream, so
what these tests exercise is the code under test rather than a hand-rolled stand-in
for httpx.

DNS is stubbed only where a hostname is the point. The address guard is tested
against literal IPs, which ``getaddrinfo`` resolves without a network.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterable
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.services import import_url
from app.services.import_url import (
    BlockedImportUrlError,
    ImportUrlEmptyError,
    ImportUrlHttpError,
    ImportUrlNotAFileError,
    ImportUrlTooLargeError,
    ImportUrlUnreachableError,
    InvalidImportUrlError,
    fetch_csv,
)

CSV = b"SKU,Total Qty.\nDD-1,5\n"

#: A genuinely global address, because the guard is strict about what counts as
#: one. The RFC 5737 documentation ranges (192.0.2.0/24, 198.51.100.0/24,
#: 203.0.113.0/24) are all reserved, so the guard refuses them — correctly, and
#: it is worth knowing that it does. Nothing connects to this: the transport is
#: mocked, so the address is only ever classified.
PUBLIC_IP = "93.184.216.34"


def settings(**overrides: Any) -> Settings:
    return Settings(env="test", **{"import_url_timeout_seconds": 5.0, **overrides})


def install(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[str]:
    """Put a MockTransport under httpx.Client. Returns the URLs requested."""
    seen: list[str] = []
    real_client = httpx.Client

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return handler(request)

    def fake_client(**kwargs: Any) -> httpx.Client:
        return real_client(**kwargs, transport=httpx.MockTransport(record))

    monkeypatch.setattr(import_url.httpx, "Client", fake_client)
    return seen


def resolve_to(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    """Make hostnames resolve to one address, without touching DNS.

    An IP literal resolves to itself, so stubbing DNS cannot accidentally make
    127.0.0.1 look public — which would quietly disarm the guard these tests
    exist to check.
    """

    def getaddrinfo(host: str, port: object, **kwargs: Any) -> list[Any]:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            resolved = address
        else:
            resolved = host
        return [(2, 1, 6, "", (resolved, port or 443))]

    monkeypatch.setattr(import_url.socket, "getaddrinfo", getaddrinfo)


def serving(body: bytes | Iterable[bytes], **kwargs: Any):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, **kwargs)

    return handler


class TestUrlValidation:
    """Refused before any connection is opened."""

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "   ",
            "file:///etc/passwd",
            "ftp://example.com/stock.csv",
            "gopher://example.com/stock.csv",
            "javascript:alert(1)",
            "https://",
            "not a url at all",
        ],
    )
    def test_rejects(self, url: str, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = install(monkeypatch, serving(CSV))

        with pytest.raises(InvalidImportUrlError):
            fetch_csv(settings(), url)

        assert seen == []  # nothing was fetched

    def test_the_scheme_is_named_in_the_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install(monkeypatch, serving(CSV))

        with pytest.raises(InvalidImportUrlError) as caught:
            fetch_csv(settings(), "file:///etc/passwd")

        assert caught.value.detail["scheme"] == "file"
        assert "http" in caught.value.next_step


class TestAddressGuard:
    """The server must not be usable as a request proxy."""

    @pytest.mark.parametrize(
        ("url", "what"),
        [
            ("http://127.0.0.1/stock.csv", "loopback"),
            ("http://127.0.0.1:8000/api/shopify/connection", "loopback, another port"),
            ("http://[::1]/stock.csv", "IPv6 loopback"),
            ("http://169.254.169.254/latest/meta-data/", "cloud metadata"),
            ("http://10.0.0.5/stock.csv", "private class A"),
            ("http://192.168.1.10/stock.csv", "private class C"),
            ("http://172.16.4.4/stock.csv", "private class B"),
            ("http://0.0.0.0/stock.csv", "unspecified"),
            ("http://[::ffff:127.0.0.1]/stock.csv", "IPv4 loopback mapped into IPv6"),
        ],
    )
    def test_internal_addresses_are_refused(
        self, url: str, what: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = install(monkeypatch, serving(CSV))

        with pytest.raises(BlockedImportUrlError):
            fetch_csv(settings(), url)

        assert seen == [], f"{what} was fetched"

    def test_a_public_looking_name_resolving_inward_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The check is on the resolved address, not on how the host is spelled."""
        seen = install(monkeypatch, serving(CSV))
        resolve_to(monkeypatch, "127.0.0.1")

        with pytest.raises(BlockedImportUrlError) as caught:
            fetch_csv(settings(), "https://totally-public.example.com/stock.csv")

        assert seen == []
        assert caught.value.detail["host"] == "totally-public.example.com"

    def test_a_redirect_inward_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A public host that redirects to loopback is the obvious way around a
        guard that only checks the URL the user typed."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "elsewhere.example.com":
                return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
            return httpx.Response(200, content=CSV)

        install(monkeypatch, handler)
        resolve_to(monkeypatch, PUBLIC_IP)

        with pytest.raises(BlockedImportUrlError):
            fetch_csv(settings(), "https://elsewhere.example.com/stock.csv")

    def test_a_name_that_does_not_resolve_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import socket as socket_module

        install(monkeypatch, serving(CSV))

        def fail(*args: Any, **kwargs: Any) -> list[Any]:
            raise socket_module.gaierror("no such host")

        monkeypatch.setattr(import_url.socket, "getaddrinfo", fail)

        with pytest.raises(ImportUrlUnreachableError) as caught:
            fetch_csv(settings(), "https://nope.example.com/stock.csv")

        assert "spelled" in caught.value.next_step


class TestDownload:
    def test_returns_the_body_and_a_csv_filename(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = install(monkeypatch, serving(CSV))
        resolve_to(monkeypatch, PUBLIC_IP)

        filename, raw = fetch_csv(settings(), "https://example.com/exports/stock.csv")

        assert raw == CSV
        assert filename == "stock.csv"
        assert seen == ["https://example.com/exports/stock.csv"]

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://example.com/exports/stock.csv", "stock.csv"),
            ("https://example.com/stock.CSV", "stock.CSV"),
            # A published Google Sheet: no extension anywhere in the path.
            ("https://docs.google.com/spreadsheets/d/abc/export?format=csv", "export.csv"),
            ("https://example.com/", "download.csv"),
            ("https://example.com", "download.csv"),
            ("https://example.com/July%20stock.csv", "July stock.csv"),
            ("https://example.com/../../etc/passwd", "passwd.csv"),
        ],
    )
    def test_filenames(self, url: str, expected: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """The name reaches Import History and the parser's extension check, so it
        always ends .csv and never carries a path."""
        install(monkeypatch, serving(CSV))
        resolve_to(monkeypatch, PUBLIC_IP)

        filename, _ = fetch_csv(settings(), url)

        assert filename == expected
        assert "/" not in filename and "\\" not in filename

    def test_follows_a_redirect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/redirect":
                return httpx.Response(302, headers={"Location": "https://example.com/real.csv"})
            return httpx.Response(200, content=CSV)

        seen = install(monkeypatch, handler)
        resolve_to(monkeypatch, PUBLIC_IP)

        _, raw = fetch_csv(settings(), "https://example.com/redirect")

        assert raw == CSV
        assert seen == ["https://example.com/redirect", "https://example.com/real.csv"]

    def test_follows_a_relative_redirect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Location may be a path; resolving it is the browser's behaviour."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/go":
                return httpx.Response(302, headers={"Location": "/files/real.csv"})
            return httpx.Response(200, content=CSV)

        seen = install(monkeypatch, handler)
        resolve_to(monkeypatch, PUBLIC_IP)

        _, raw = fetch_csv(settings(), "https://example.com/go")

        assert raw == CSV
        assert seen[-1] == "https://example.com/files/real.csv"

    def test_a_redirect_loop_ends(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"Location": "https://example.com/round"})

        seen = install(monkeypatch, handler)
        resolve_to(monkeypatch, PUBLIC_IP)

        with pytest.raises(ImportUrlHttpError) as caught:
            fetch_csv(settings(), "https://example.com/round")

        assert caught.value.detail["max_redirects"] == import_url.MAX_REDIRECTS
        assert len(seen) == import_url.MAX_REDIRECTS + 1

    def test_a_redirect_with_no_location_is_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install(monkeypatch, lambda _: httpx.Response(302))
        resolve_to(monkeypatch, PUBLIC_IP)

        with pytest.raises(ImportUrlHttpError):
            fetch_csv(settings(), "https://example.com/stock.csv")


class TestFailures:
    @pytest.mark.parametrize(
        ("status", "phrase"),
        [
            (401, "sign-in"),
            (403, "sign-in"),
            (404, "no file at that link"),
            (500, "error instead of a file"),
            (503, "error instead of a file"),
        ],
    )
    def test_http_status_becomes_a_readable_message(
        self, status: int, phrase: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, lambda _: httpx.Response(status))
        resolve_to(monkeypatch, PUBLIC_IP)

        with pytest.raises(ImportUrlHttpError) as caught:
            fetch_csv(settings(), "https://example.com/stock.csv")

        assert phrase in caught.value.message
        assert caught.value.detail["status"] == status

    def test_a_dead_connection_is_one_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DNS, TLS and timeout failures are one problem from the user's side."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        install(monkeypatch, handler)
        resolve_to(monkeypatch, PUBLIC_IP)

        with pytest.raises(ImportUrlUnreachableError):
            fetch_csv(settings(), "https://example.com/stock.csv")

    def test_a_timeout_is_the_same(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow", request=request)

        install(monkeypatch, handler)
        resolve_to(monkeypatch, PUBLIC_IP)

        with pytest.raises(ImportUrlUnreachableError):
            fetch_csv(settings(), "https://example.com/stock.csv")

    @pytest.mark.parametrize(
        "body",
        [
            b"<!DOCTYPE html><html><body>Sign in</body></html>",
            b"<html><head><title>Google Drive</title></head></html>",
            b"\n  <!doctype HTML>\n<html>",
            b"<?xml version='1.0'?><feed/>",
        ],
    )
    def test_a_web_page_is_not_a_csv(self, body: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
        """A sign-in wall would otherwise be reported as a missing SKU column."""
        install(monkeypatch, serving(body))
        resolve_to(monkeypatch, PUBLIC_IP)

        with pytest.raises(ImportUrlNotAFileError) as caught:
            fetch_csv(settings(), "https://example.com/stock.csv")

        assert "Publish to web" in caught.value.next_step

    @pytest.mark.parametrize("body", [b"", b"   \n\n  "])
    def test_an_empty_body_is_refused(self, body: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
        install(monkeypatch, serving(body))
        resolve_to(monkeypatch, PUBLIC_IP)

        with pytest.raises(ImportUrlEmptyError):
            fetch_csv(settings(), "https://example.com/stock.csv")


class TestSizeLimit:
    def test_refused_on_content_length_before_downloading(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        oversized = 2 * 1024 * 1024  # max_upload_mb has a floor of 1
        install(monkeypatch, serving(b"x" * oversized))
        resolve_to(monkeypatch, PUBLIC_IP)

        with pytest.raises(ImportUrlTooLargeError) as caught:
            fetch_csv(settings(max_upload_mb=1), "https://example.com/stock.csv")

        assert caught.value.detail["size_bytes"] == oversized
        assert caught.value.detail["limit_bytes"] == 1024 * 1024

    def test_refused_mid_stream_when_no_length_is_declared(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A server that omits Content-Length must not decide our memory use."""
        chunks = (b"x" * 8192 for _ in range(1000))
        install(monkeypatch, serving(chunks))
        resolve_to(monkeypatch, PUBLIC_IP)

        with pytest.raises(ImportUrlTooLargeError):
            fetch_csv(settings(max_upload_mb=1), "https://example.com/stock.csv")

    def test_a_file_inside_the_limit_is_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = b"SKU,Total Qty.\n" + b"".join(f"DD-{i},1\n".encode() for i in range(5000))
        install(monkeypatch, serving(body))
        resolve_to(monkeypatch, PUBLIC_IP)

        _, raw = fetch_csv(settings(), "https://example.com/stock.csv")

        assert raw == body


def test_the_configured_timeout_is_the_one_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not the httpx default of 5s: a large sheet over a slow link needs longer."""
    captured: dict[str, Any] = {}
    real_client = httpx.Client

    def fake_client(**kwargs: Any) -> httpx.Client:
        captured.update(kwargs)
        return real_client(
            **kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=CSV))
        )

    monkeypatch.setattr(import_url.httpx, "Client", fake_client)
    resolve_to(monkeypatch, PUBLIC_IP)

    fetch_csv(settings(import_url_timeout_seconds=42.0), "https://example.com/stock.csv")

    assert captured["timeout"] == 42.0
    assert captured["follow_redirects"] is False  # hops are validated by hand
