"""TLS-only STAGING notification transport; never called in a public request."""
from __future__ import annotations

import os
import smtplib
import ssl
import uuid
from dataclasses import dataclass, field
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate
from .configuration import require_production_chain, PRODUCTION_CHAIN_TASK, PRODUCTION_SYNTHETIC_MARKER, public_intake_enabled

RECIPIENT = os.getenv('INQUIRY_EXPECTED_NOTIFY_TO','notifications@example.invalid')
TRUTH_NOTICE = ('This is buyer-submitted information. It does not establish supplier capability, '
                'production feasibility or certification availability.')
FIELDS = ('company','name','work_email','country','whatsapp','series','product_type',
          'starting_point','product_direction','target_market','expected_quantity','launch_timeline')
FIELDS_BY_TYPE = {
    'PRODUCT_BRIEF': FIELDS,
    'SAMPLE_REQUEST': ('name','company','work_email','email','country','whatsapp','product','quantity','expected_quantity','formula_preference','target_market','packaging_preference','launch_timeline','reference_product','message'),
    'RFQ': ('contact_name','name','company','work_email','email','country','whatsapp','website_social','product_category','product','product_idea','quantity','expected_quantity','formula_route','target_market','target_price_range','packaging_requirement','launch_date','certification_testing_requirement','reference_product','additional_notes','message'),
}
SAFE_DELIVERY_ERRORS = frozenset({'WAITING_SMTP_SECRET','SMTP_TLS_ERROR','SMTP_AUTH_ERROR',
    'SMTP_SEND_ERROR','SMTP_CONNECTION_ERROR','INVALID_NOTIFICATION','WORKER_ERROR'})


class NotificationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def mailbox(value: str) -> str:
    if not value or '\r' in value or '\n' in value or len(value) > 254:
        raise NotificationError('INVALID_NOTIFICATION')
    try:
        address = Address(addr_spec=value)
        if not address.username or not address.domain or '.' not in address.domain:
            raise ValueError()
        return address.addr_spec
    except (ValueError, IndexError):
        raise NotificationError('INVALID_NOTIFICATION') from None


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    username: str
    password: str = field(repr=False)
    security: str
    sender: str
    recipient: str
    deployment: str = 'STAGING'

    @classmethod
    def from_environment(cls):
        deployment = os.getenv('APP_ENV')
        if deployment not in {'STAGING','PRODUCTION'}:
            raise NotificationError('INVALID_NOTIFICATION')
        if deployment == 'PRODUCTION':
            try:
                require_production_chain()
            except RuntimeError:
                raise NotificationError('INVALID_NOTIFICATION') from None
            if os.getenv('PRODUCTION_SYNTHETIC_MAIL_AUTHORIZATION') != PRODUCTION_CHAIN_TASK:
                raise NotificationError('INVALID_NOTIFICATION')
        names = ('SMTP_HOST','SMTP_PORT','SMTP_USERNAME','SMTP_PASSWORD','SMTP_SECURITY','SMTP_FROM','INQUIRY_NOTIFY_TO')
        if any(not os.getenv(name) for name in names):
            raise NotificationError('WAITING_SMTP_SECRET')
        try:
            config = cls(os.environ['SMTP_HOST'],int(os.environ['SMTP_PORT']),os.environ['SMTP_USERNAME'],
                         os.environ['SMTP_PASSWORD'],os.environ['SMTP_SECURITY'].lower(),
                         mailbox(os.environ['SMTP_FROM']),mailbox(os.environ['INQUIRY_NOTIFY_TO']),deployment)
        except ValueError:
            raise NotificationError('INVALID_NOTIFICATION') from None
        if config.security not in {'starttls','ssl'} or not 1 <= config.port <= 65535 or config.recipient != RECIPIENT:
            raise NotificationError('INVALID_NOTIFICATION')
        return config


def build_message(record, outbox_id: str, config: SMTPConfig) -> EmailMessage:
    record_id = str(uuid.UUID(record.record_id))
    outbox = str(uuid.UUID(outbox_id))
    fields = record.submitted_fields
    message = EmailMessage()
    message['From'] = mailbox(config.sender)
    message['To'] = mailbox(config.recipient)
    message['Reply-To'] = mailbox(str(fields.get('work_email', fields.get('email', ''))))
    kind = {'PRODUCT_BRIEF':'Product Brief','RFQ':'RFQ','SAMPLE_REQUEST':'Sample Request'}[record.record_type.value]
    production = config.deployment == 'PRODUCTION'
    public_production = production and public_intake_enabled()
    if production:
        require_production_chain()
    if production and not public_production:
        if (record.source_context.get('source_page') != PRODUCTION_SYNTHETIC_MARKER or
                fields.get('company') != 'B2B PRODUCTION SYNTHETIC' or
                fields.get('work_email') != 'synthetic@example.invalid'):
            raise NotificationError('INVALID_NOTIFICATION')
    prefix = 'B2B' if public_production else 'PRODUCTION SYNTHETIC TEST' if production else 'STAGING'
    message['Subject'] = f'[{prefix}] New {os.getenv("SITE_NAME","B2B")} {kind} · {record_id[:8]}'
    message['Date'] = formatdate(localtime=False, usegmt=True)
    message_scope = 'production' if public_production else 'production-synthetic' if production else 'staging'
    message['Message-ID'] = f'<b2b-{message_scope}-{outbox}@{os.getenv("TARGET_DOMAIN","demo.example")}>'
    notice = 'New customer inquiry submitted via '+os.getenv('TARGET_DOMAIN','demo.example') if public_production else f'{prefix} / SYNTHETIC TEST — NOT A CUSTOMER INQUIRY'
    lines = [notice,
             f'Inquiry type: {kind}', f'Inquiry ID: {record_id}', f'Created: {record.created_at}', '']
    for key in FIELDS_BY_TYPE[record.record_type.value]:
        value = fields.get(key)
        if value:
            lines.append(f'{key.replace("_", " ").title()}: {str(value)[:500]}')
    lines.extend(['',TRUTH_NOTICE])
    message.set_content('\n'.join(lines))
    return message


class SMTPNotificationAdapter:
    def __init__(self, config: SMTPConfig, *, smtp_factory=None, ssl_factory=None):
        self.config = config
        self.smtp_factory = smtp_factory or smtplib.SMTP
        self.ssl_factory = ssl_factory or smtplib.SMTP_SSL

    def send(self, record, outbox_id: str) -> None:
        if self.config.security not in {'starttls','ssl'} or self.config.recipient != RECIPIENT:
            raise NotificationError('INVALID_NOTIFICATION')
        message = build_message(record, outbox_id, self.config)
        context = ssl.create_default_context()
        try:
            factory = self.ssl_factory if self.config.security == 'ssl' else self.smtp_factory
            kwargs = {'context':context} if self.config.security == 'ssl' else {}
            with factory(self.config.host, self.config.port, timeout=30, **kwargs) as client:
                client.ehlo()
                if self.config.security == 'starttls':
                    client.starttls(context=context)
                    client.ehlo()
                client.login(self.config.username, self.config.password)
                refused = client.send_message(message,from_addr=self.config.sender,to_addrs=[self.config.recipient])
                if refused:
                    raise NotificationError('SMTP_SEND_ERROR')
        except ssl.SSLError:
            raise NotificationError('SMTP_TLS_ERROR') from None
        except smtplib.SMTPAuthenticationError:
            raise NotificationError('SMTP_AUTH_ERROR') from None
        except smtplib.SMTPException:
            raise NotificationError('SMTP_SEND_ERROR') from None
        except OSError:
            raise NotificationError('SMTP_CONNECTION_ERROR') from None
