"""Email alerting: one summary email per run, only when there were failures.

Secrets are read from environment variables (GitHub Secrets in CI):
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr
from html import escape

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    sender: str
    recipients: tuple[str, ...]

    @classmethod
    def from_env(cls) -> SmtpConfig | None:
        host = os.environ.get("SMTP_HOST")
        to = os.environ.get("ALERT_EMAIL_TO")
        if not host or not to:
            return None
        user = os.environ.get("SMTP_USER", "")
        return cls(
            host=host,
            port=int(os.environ.get("SMTP_PORT", "587")),
            user=user,
            password=os.environ.get("SMTP_PASSWORD", ""),
            sender=os.environ.get("SMTP_FROM", user or "indicadores@localhost"),
            recipients=tuple(part.strip() for part in to.split(",") if part.strip()),
        )


@dataclass(frozen=True)
class FailureItem:
    slug: str
    source: str
    error: str
    forward_filled_value: float | None
    status: str
    stale_warning: bool = False


def build_subject(run_date: date, failures: int) -> str:
    return f"[INDICADORES] {run_date.isoformat()} – {failures} fallo(s)"


def _format_value(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:g}"


def build_email(
    run_date: date,
    failures: list[FailureItem],
    stale_warnings: list[FailureItem] | None = None,
    run_url: str | None = None,
    smtp: SmtpConfig | None = None,
) -> EmailMessage:
    """Build the single summary email (HTML + plain text fallback)."""

    if smtp is None:
        smtp = SmtpConfig.from_env()
    if smtp is None:
        raise RuntimeError("SMTP configuration is not available")

    message = EmailMessage()
    message["Subject"] = build_subject(run_date, len(failures))
    message["From"] = formataddr(("Indicadores ETL", smtp.sender))
    message["To"] = ", ".join(smtp.recipients)

    rows = "".join(
        "<tr>"
        f"<td>{escape(item.slug)}</td>"
        f"<td>{escape(item.source)}</td>"
        f"<td>{escape(item.error)}</td>"
        f"<td>{escape(_format_value(item.forward_filled_value))}</td>"
        f"<td>{escape(item.status)}</td>"
        "</tr>"
        for item in failures
    )
    stale_rows = "".join(
        f"<li>{escape(item.slug)}: source_date repetida ({escape(item.error)})</li>"
        for item in (stale_warnings or [])
    )
    run_link = (
        f'<p><a href="{escape(run_url)}">Ver run en GitHub Actions</a></p>' if run_url else ""
    )
    stale_block = f"<h3>Avisos de dato stale</h3><ul>{stale_rows}</ul>" if stale_rows else ""
    html_body = f"""
    <html><body>
    <p>Resumen del run de indicadores del <b>{run_date.isoformat()}</b>.</p>
    <p>{len(failures)} indicador(es) con fallo. Se aplicó forward-fill con el
    último dato disponible (status <code>forward_filled</code>).</p>
    <table border="1" cellpadding="6" cellspacing="0">
      <thead>
        <tr><th>Indicador</th><th>Fuente</th><th>Error</th>
        <th>Valor forward-fill</th><th>Status</th></tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    {stale_block}
    {run_link}
    </body></html>
    """
    text_lines = [
        f"Resumen indicadores {run_date.isoformat()}",
        f"Fallos: {len(failures)}",
        "",
    ]
    for item in failures:
        text_lines.append(
            f"- {item.slug} ({item.source}): {item.error} | "
            f"forward-fill={_format_value(item.forward_filled_value)} | {item.status}"
        )
    if stale_warnings:
        text_lines.append("")
        text_lines.append("Avisos de dato stale:")
        for item in stale_warnings:
            text_lines.append(f"- {item.slug}: source_date repetida ({item.error})")
    if run_url:
        text_lines.append("")
        text_lines.append(f"Run: {run_url}")

    message.set_content("\n".join(text_lines))
    message.add_alternative(html_body, subtype="html")
    return message


def send_email(message: EmailMessage, smtp: SmtpConfig) -> None:
    """Send via SMTP with STARTTLS."""

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp.host, smtp.port, timeout=30) as server:
        server.starttls(context=context)
        if smtp.user:
            server.login(smtp.user, smtp.password)
        server.send_message(message)


Sender = Callable[[EmailMessage, SmtpConfig], None]


def maybe_send_alert(
    run_date: date,
    failures: list[FailureItem],
    stale_warnings: list[FailureItem] | None = None,
    run_url: str | None = None,
    smtp: SmtpConfig | None = None,
    sender: Sender = send_email,
) -> bool:
    """Send exactly one summary email if there were failures.

    Returns True when an email was sent. Never raises on delivery problems:
    alerting failures must not mask the ETL result.
    """

    if not failures:
        return False
    if smtp is None:
        smtp = SmtpConfig.from_env()
    if smtp is None:
        logger.error("failures detected but SMTP is not configured; email skipped")
        return False
    message = build_email(run_date, failures, stale_warnings, run_url, smtp)
    try:
        sender(message, smtp)
    except Exception:  # noqa: BLE001 - alerting must never crash the ETL
        logger.exception("failed to send alert email")
        return False
    logger.info("alert email sent to %s", ", ".join(smtp.recipients))
    return True
