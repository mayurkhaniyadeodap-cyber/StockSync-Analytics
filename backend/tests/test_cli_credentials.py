"""How the CLI obtains a password, and how it must fail when it cannot.

This file exists because the previous answer to "what password does the
administrator get" was a constant in `app/cli.py`. It was written as a
development convenience, documented in the README, and it reached production —
so the admin credential of a live, internet-facing deployment was a literal in
a public repository, and nothing in the test suite objected.

These tests are the objection. The rule they encode: an account is created with
a password the operator supplied, or it is not created at all.
"""

from __future__ import annotations

import getpass
from typing import Any

import pytest

from app.cli import ADMIN_PASSWORD_ENV, MIN_PASSWORD_LENGTH, _read_password

GOOD = "a-perfectly-fine-password"


@pytest.fixture(autouse=True)
def _no_ambient_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own shell must not decide what these tests prove."""
    monkeypatch.delenv(ADMIN_PASSWORD_ENV, raising=False)


class TestNoBuiltInPassword:
    def test_the_module_defines_no_default_password(self) -> None:
        """The regression, stated directly.

        Named rather than searched for by value: a future constant under a
        different name is the same mistake, and this catches it by shape.
        """
        import app.cli as cli

        suspects = [
            name
            for name in dir(cli)
            if "PASSWORD" in name and name not in {"ADMIN_PASSWORD_ENV", "MIN_PASSWORD_LENGTH"}
        ]
        assert suspects == [], f"cli.py grew a password constant: {suspects}"

    def test_no_terminal_and_no_variable_is_a_clear_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Never invent one, and never hang — say what to set."""

        def no_terminal(_: str = "") -> str:
            raise EOFError

        monkeypatch.setattr(getpass, "getpass", no_terminal)

        with pytest.raises(SystemExit) as exit_info:
            _read_password()

        message = str(exit_info.value)
        assert ADMIN_PASSWORD_ENV in message
        assert "--password-env" in message


class TestReadingFromTheEnvironment:
    def test_the_named_variable_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEPLOY_SECRET", GOOD)

        assert _read_password("DEPLOY_SECRET") == GOOD

    def test_a_named_but_unset_variable_fails_instead_of_prompting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A scripted caller asked for that variable. Blocking on a prompt in a
        deployment script is worse than stopping with the reason."""
        monkeypatch.delenv("DEPLOY_SECRET", raising=False)
        monkeypatch.setattr(getpass, "getpass", lambda _="": pytest.fail("prompted anyway"))

        with pytest.raises(SystemExit, match="DEPLOY_SECRET"):
            _read_password("DEPLOY_SECRET")

    def test_the_conventional_variable_is_the_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """What lets `reset-db` rebuild unattended without a password in the repo."""
        monkeypatch.setenv(ADMIN_PASSWORD_ENV, GOOD)

        assert _read_password() == GOOD

    def test_an_explicit_name_wins_over_the_convention(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ADMIN_PASSWORD_ENV, "the-conventional-one")
        monkeypatch.setenv("DEPLOY_SECRET", "the-named-one")

        assert _read_password("DEPLOY_SECRET") == "the-named-one"

    @pytest.mark.parametrize("name", [None, "DEPLOY_SECRET"])
    def test_a_short_value_is_refused_from_either_variable(
        self, monkeypatch: pytest.MonkeyPatch, name: str | None
    ) -> None:
        monkeypatch.setenv(name or ADMIN_PASSWORD_ENV, "x" * (MIN_PASSWORD_LENGTH - 1))

        with pytest.raises(SystemExit, match=str(MIN_PASSWORD_LENGTH)):
            _read_password(name)


class TestPrompting:
    def test_it_asks_twice_and_keeps_the_matching_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        answers = iter([GOOD, GOOD])
        monkeypatch.setattr(getpass, "getpass", lambda _="": next(answers))

        assert _read_password() == GOOD

    def test_a_mismatch_asks_again_rather_than_accepting_the_first(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        answers = iter([GOOD, "something-else-entirely", GOOD, GOOD])
        monkeypatch.setattr(getpass, "getpass", lambda _="": next(answers))

        assert _read_password() == GOOD
        assert "didn't match" in capsys.readouterr().out

    def test_a_short_answer_asks_again(self, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
        answers = iter(["short", GOOD, GOOD])
        monkeypatch.setattr(getpass, "getpass", lambda _="": next(answers))

        assert _read_password() == GOOD
        assert "Too short" in capsys.readouterr().out

    def test_the_password_never_reaches_stdout(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """`seed` used to print it. Shell history, CI logs and journald all
        outlive the terminal that ran the command."""
        answers = iter(["short", GOOD, GOOD])
        monkeypatch.setattr(getpass, "getpass", lambda _="": next(answers))

        _read_password()

        assert GOOD not in capsys.readouterr().out
