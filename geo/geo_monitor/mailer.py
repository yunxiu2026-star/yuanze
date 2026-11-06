"""Optional: e-mail the weekly report (only when SMTP_* settings are filled in)."""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

from .config import Config

log = logging.getLogger(__name__)


def send_report(cfg: Config, path: str, summary: str) -> bool:
    if not (cfg.smtp_host and cfg.smtp_user and cfg.report_email_to):
        return False
    msg = EmailMessage()
    msg["Subject"] = f"{cfg.target_domain} AI 推荐监测周报"
    msg["From"] = cfg.smtp_user
    msg["To"] = ", ".join(cfg.report_email_to)
    msg.set_content(summary + "\n\n详细数据见附件。")
    with open(path, "rb") as fh:
        msg.add_attachment(fh.read(), maintype="application",
                           subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           filename=os.path.basename(path))
    try:
        if cfg.smtp_port == 465:
            with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=ssl.create_default_context(),
                                  timeout=60) as s:
                s.login(cfg.smtp_user, cfg.smtp_password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=60) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(cfg.smtp_user, cfg.smtp_password)
                s.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        log.error("周报邮件发送失败: %s", exc)
        return False
    log.info("周报已发送到 %s", ", ".join(cfg.report_email_to))
    return True
