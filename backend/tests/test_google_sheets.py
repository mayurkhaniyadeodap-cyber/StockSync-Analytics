"""Google Sheet import: URL translation, and how Google's refusals read.

The translation is a pure function and is tested as one — every shape a user can
copy out of a browser, plus the ones they cannot. The download is
``import_url.fetch_csv``, already covered in test_import_url.py; what is asserted
here is that its failures come out in language about sheets.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.services import google_sheets, import_url
from app.services.google_sheets import (
    NotAGoogleSheetUrlError,
    SheetNotPublicError,
    export_url,
    fetch,
)

CSV = b"SKU,Total Qty.\nDD-1,5\n"
ID = "1AbC_dEf-GhIjKlMnOpQrStUvWxYz0123456789ab"
BASE = "https://docs.google.com/spreadsheets"


def settings(**overrides: Any) -> Settings:
    return Settings(env="test", **overrides)


def install(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[str]:
    """Mock the transport under httpx.Client and stub DNS. Returns URLs asked for."""
    seen: list[str] = []
    real_client = httpx.Client

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return handler(request)

    def client(**kwargs: Any) -> httpx.Client:
        return real_client(**kwargs, transport=httpx.MockTransport(record))

    def getaddrinfo(host: str, port: object, **kwargs: Any) -> list[Any]:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return [(2, 1, 6, "", ("142.250.183.78", port or 443))]
        return [(2, 1, 6, "", (host, port or 443))]

    monkeypatch.setattr(import_url.httpx, "Client", client)
    monkeypatch.setattr(import_url.socket, "getaddrinfo", getaddrinfo)
    return seen


class TestExportUrl:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            # The address the browser shows while editing — the common case.
            (f"{BASE}/d/{ID}/edit#gid=456", f"{BASE}/d/{ID}/export?format=csv&gid=456"),
            # No tab named: leave gid out rather than assuming 0.
            (f"{BASE}/d/{ID}/edit", f"{BASE}/d/{ID}/export?format=csv"),
            (f"{BASE}/d/{ID}", f"{BASE}/d/{ID}/export?format=csv"),
            (f"{BASE}/d/{ID}/view", f"{BASE}/d/{ID}/export?format=csv"),
            # A share link, with the tracking parameter Google appends.
            (
                f"{BASE}/d/{ID}/edit?usp=sharing#gid=99",
                f"{BASE}/d/{ID}/export?format=csv&gid=99",
            ),
            # gid in the query rather than the fragment.
            (f"{BASE}/d/{ID}/edit?gid=7", f"{BASE}/d/{ID}/export?format=csv&gid=7"),
            # An export URL the user built already.
            (
                f"{BASE}/d/{ID}/export?format=csv&gid=0",
                f"{BASE}/d/{ID}/export?format=csv&gid=0",
            ),
            # No scheme: a pasted address usually has none.
            (f"docs.google.com/spreadsheets/d/{ID}/edit", f"{BASE}/d/{ID}/export?format=csv"),
            ("  " + f"{BASE}/d/{ID}/edit" + "  ", f"{BASE}/d/{ID}/export?format=csv"),
        ],
    )
    def test_translates(self, given: str, expected: str) -> None:
        assert export_url(given)[0] == expected

    def test_the_multi_account_prefix_is_dropped(self) -> None:
        """`u/<n>/` appears when several Google accounts are signed in.

        It selects an account, which is meaningless server-side, so the export
        URL is built without it.
        """
        built, key = export_url(f"{BASE}/u/2/d/{ID}/edit#gid=1")

        assert built == f"{BASE}/d/{ID}/export?format=csv&gid=1"
        assert key == ID

    def test_returns_the_document_key(self) -> None:
        _, key = export_url(f"{BASE}/d/{ID}/edit")

        assert key == ID

    def test_a_published_link_uses_the_other_export_path(self) -> None:
        """`/d/e/<key>` is a different namespace: /pub?output=csv, not /export."""
        built, key = export_url(f"{BASE}/d/e/2PACX-1vQx_yZabcdef/pubhtml?gid=3")

        assert built == f"{BASE}/d/e/2PACX-1vQx_yZabcdef/pub?output=csv&gid=3"
        assert key == "2PACX-1vQx_yZabcdef"

    def test_a_published_link_is_not_mistaken_for_a_normal_one(self) -> None:
        """Its key matches the general pattern too, so order of testing matters."""
        built, _ = export_url(f"{BASE}/d/e/2PACX-1vQx_yZabcdef/pubhtml")

        assert "/d/e/" in built
        assert "format=csv" not in built

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "   ",
            "not a url",
            "https://example.com/spreadsheets/d/abc/edit",  # right path, wrong host
            "https://docs.google.com/document/d/abc/edit",  # a Doc, not a Sheet
            "https://docs.google.com/spreadsheets/",  # no document id
            "https://docs.google.com/spreadsheets/d/short/edit",  # id too short
            "file:///etc/passwd",
            "https://docs.google.com.evil.test/spreadsheets/d/abcdefghij/edit",
        ],
    )
    def test_refuses(self, url: str) -> None:
        with pytest.raises(NotAGoogleSheetUrlError):
            export_url(url)

    def test_the_refusal_says_what_to_do(self) -> None:
        with pytest.raises(NotAGoogleSheetUrlError) as caught:
            export_url("https://example.com/sheet")

        assert "copy the address" in caught.value.next_step
        assert caught.value.detail["received"] == "https://example.com/sheet"

    def test_a_lookalike_host_is_refused(self) -> None:
        """`docs.google.com.evil.test` ends with the real host as a substring."""
        with pytest.raises(NotAGoogleSheetUrlError):
            export_url(f"https://docs.google.com.evil.test/spreadsheets/d/{ID}/edit")

    def test_a_non_numeric_gid_is_ignored_not_forwarded(self) -> None:
        built, _ = export_url(f"{BASE}/d/{ID}/edit#gid=abc")

        assert built == f"{BASE}/d/{ID}/export?format=csv"


class TestFetch:
    def test_downloads_from_the_export_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = install(monkeypatch, lambda _: httpx.Response(200, content=CSV))

        _, raw = fetch(settings(), f"{BASE}/d/{ID}/edit#gid=5")

        assert raw == CSV
        assert seen == [f"{BASE}/d/{ID}/export?format=csv&gid=5"]

    def test_the_filename_identifies_the_sheet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Two sheets must be distinguishable in Import History."""
        install(monkeypatch, lambda _: httpx.Response(200, content=CSV))

        first, _ = fetch(settings(), f"{BASE}/d/{ID}/edit")
        second, _ = fetch(settings(), f"{BASE}/d/2ZzZ_YyY-XxWwVvUuTt/edit")

        assert first == f"google-sheet-{ID[:12]}.csv"
        assert first != second
        assert first.endswith(".csv")  # the parser dispatches on the extension

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_refused_export_is_the_friendly_message(
        self, status: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, lambda _: httpx.Response(status))

        with pytest.raises(SheetNotPublicError) as caught:
            fetch(settings(), f"{BASE}/d/{ID}/edit")

        assert caught.value.message == (
            "This Google Sheet isn't publicly accessible. Please make it viewable by "
            "anyone with the link or upload it as a CSV file."
        )
        assert "Anyone with the link" in caught.value.next_step

    def test_a_sign_in_page_is_the_same_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The usual shape: Google 302s a private sheet to a sign-in page.

        The download then *succeeds* and returns HTML, which the generic fetcher
        reports as "not a file". To the user that is the same problem as a 403.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "accounts.google.com":
                return httpx.Response(200, content=b"<!DOCTYPE html><html>Sign in")
            return httpx.Response(
                302, headers={"Location": "https://accounts.google.com/ServiceLogin?x=1"}
            )

        install(monkeypatch, handler)

        with pytest.raises(SheetNotPublicError):
            fetch(settings(), f"{BASE}/d/{ID}/edit")

    def test_a_sign_in_bounce_chain_is_the_same_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Auth flows can redirect more times than the fetcher will follow."""
        install(
            monkeypatch,
            lambda _: httpx.Response(
                302, headers={"Location": "https://accounts.google.com/ServiceLogin"}
            ),
        )

        with pytest.raises(SheetNotPublicError):
            fetch(settings(), f"{BASE}/d/{ID}/edit")

    def test_a_deleted_sheet_is_not_reported_as_private(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Telling someone to share a sheet that no longer exists wastes their time."""
        install(monkeypatch, lambda _: httpx.Response(404))

        with pytest.raises(import_url.ImportUrlHttpError) as caught:
            fetch(settings(), f"{BASE}/d/{ID}/edit")

        assert not isinstance(caught.value, SheetNotPublicError)
        assert caught.value.detail["status"] == 404

    def test_other_failures_keep_their_own_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install(monkeypatch, lambda _: httpx.Response(500))

        with pytest.raises(import_url.ImportUrlHttpError) as caught:
            fetch(settings(), f"{BASE}/d/{ID}/edit")

        assert not isinstance(caught.value, SheetNotPublicError)

    def test_an_unreachable_google_is_not_a_permissions_problem(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        install(monkeypatch, handler)

        with pytest.raises(import_url.ImportUrlUnreachableError):
            fetch(settings(), f"{BASE}/d/{ID}/edit")

    def test_an_empty_sheet_is_refused_by_the_shared_fetcher(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, lambda _: httpx.Response(200, content=b""))

        with pytest.raises(import_url.ImportUrlEmptyError):
            fetch(settings(), f"{BASE}/d/{ID}/edit")

    def test_the_size_cap_still_applies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The sheet path must not be a way around the upload limit."""
        install(monkeypatch, lambda _: httpx.Response(200, content=b"x" * (2 * 1024 * 1024)))

        with pytest.raises(import_url.ImportUrlTooLargeError):
            fetch(settings(max_upload_mb=1), f"{BASE}/d/{ID}/edit")

    def test_nothing_is_fetched_for_a_url_that_is_not_a_sheet(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = install(monkeypatch, lambda _: httpx.Response(200, content=CSV))

        with pytest.raises(NotAGoogleSheetUrlError):
            fetch(settings(), "https://example.com/stock.csv")

        assert seen == []

    def test_the_address_guard_is_the_shared_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Host is pinned to docs.google.com, so there is nothing to smuggle."""
        assert google_sheets._HOSTS == frozenset({"docs.google.com", "www.docs.google.com"})
