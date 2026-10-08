"""Alert email tests with a fake SMTP sender (never a real network call)."""

from __future__ import annotations

from datetime import date

from etl.alerts import (
    FailureItem,
    SmtpConfig,
    build_email,
    build_subject,
    maybe_send_alert,
)


def smtp_config() -> SmtpConfig:
    return SmtpConfig(
        host="smtp.test",
        port=2525,
        user="user",
        password="secret",
        sender="etl@test",
        recipients=("owner@test",),
    )


def failure(slug: str = "euro", status: str = "forward_filled") -> FailureItem:
    return FailureItem(
        slug=slug,
        source="larepublica_main",
        error="timeout after 3 attempts",
        forward_filled_value=3625.6,
        status=status,
    )


def test_subject_format() -> None:
    assert build_subject(date(2026, 10, 8), 3) == "[INDICADORES] 2026-10-08 – 3 fallo(s)"


def test_email_contains_table_and_run_link() -> None:
    message = build_email(
        date(2026, 10, 8),
        [failure()],
        [],
        run_url="https://github.com/org/repo/actions/runs/1",
        smtp=smtp_config(),
    )
    html = message.get_body(preferencelist=("html",)).get_content()
    assert message["Subject"] == "[INDICADORES] 2026-10-08 – 1 fallo(s)"
    assert "euro" in html
    assert "larepublica_main" in html
    assert "timeout after 3 attempts" in html
    assert "forward_filled" in html
    assert "actions/runs/1" in html
    assert message["To"] == "owner@test"


def test_email_includes_stale_warnings() -> None:
    stale = FailureItem(
        slug="uvr",
        source="larepublica_main",
        error="source_date=2026-10-07",
        forward_filled_value=419.29,
        status="stale",
        stale_warning=True,
    )
    message = build_email(date(2026, 10, 8), [failure()], [stale], smtp=smtp_config())
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "stale" in html.lower()
    assert "uvr" in html


def test_no_failures_means_no_email() -> None:
    calls: list[object] = []
    sent = maybe_send_alert(
        date(2026, 10, 8),
        [],
        [],
        smtp=smtp_config(),
        sender=lambda message, smtp: calls.append(message),
    )
    assert sent is False
    assert calls == []


def test_failures_send_one_email() -> None:
    calls: list[object] = []
    sent = maybe_send_alert(
        date(2026, 10, 8),
        [failure()],
        [],
        smtp=smtp_config(),
        sender=lambda message, smtp: calls.append(message),
    )
    assert sent is True
    assert len(calls) == 1


def test_sender_failure_is_not_fatal() -> None:
    def broken_sender(message: object, smtp: object) -> None:
        raise RuntimeError("smtp down")

    sent = maybe_send_alert(
        date(2026, 10, 8),
        [failure()],
        [],
        smtp=smtp_config(),
        sender=broken_sender,
    )
    assert sent is False


def test_missing_smtp_config_skips_email(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    for key in ("SMTP_HOST", "ALERT_EMAIL_TO", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    sent = maybe_send_alert(
        date(2026, 10, 8),
        [failure()],
        [],
        smtp=None,
        sender=lambda message, smtp: None,
    )
    assert sent is False


def test_send_email_uses_starttls_and_login(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from etl import alerts

    events: list[str] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            events.append(f"connect {host}:{port}")

        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(self, *args: object) -> bool:
            events.append("close")
            return False

        def starttls(self, context: object) -> None:
            events.append("starttls")

        def login(self, user: str, password: str) -> None:
            events.append(f"login {user}")

        def send_message(self, message: object) -> None:
            events.append("send")

    monkeypatch.setattr(alerts.smtplib, "SMTP", FakeSMTP)
    message = build_email(date(2026, 10, 8), [failure()], smtp=smtp_config())
    alerts.send_email(message, smtp_config())
    assert events == [
        "connect smtp.test:2525",
        "starttls",
        "login user",
        "send",
        "close",
    ]


def test_smtp_config_from_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from etl.alerts import SmtpConfig

    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("ALERT_EMAIL_TO", "a@x.com, b@x.com")
    config = SmtpConfig.from_env()
    assert config is not None
    assert config.port == 465
    assert config.recipients == ("a@x.com", "b@x.com")
    monkeypatch.delenv("SMTP_HOST")
    assert SmtpConfig.from_env() is None
