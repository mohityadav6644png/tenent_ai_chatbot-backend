import pytest
from app.core import email
from app.core.config import settings


@pytest.mark.asyncio
async def test_tenant_onboarding_email_includes_personalized_invite(monkeypatch):
    class RecordingSender:
        def __init__(self):
            self.message = None

        async def send_email(self, recipient, subject, text_body, html_body):
            self.message = (recipient, subject, text_body, html_body)
            return True

    sender = RecordingSender()
    monkeypatch.setattr(email, "get_email_sender", lambda: sender)
    sent = await email.send_tenant_onboarding_email(
        "admin@example.com",
        "Jane <Doe>",
        "secure-token",
        "Acme & Co",
    )

    assert sent is True
    recipient, subject, text_body, html_body = sender.message
    assert recipient == "admin@example.com"
    assert subject == "Welcome to Your SaaS Platform - Acme & Co Onboarded Successfully!"
    assert "Hello Jane <Doe>" in text_body
    invite_url = f"{settings.FRONTEND_BASE_URL}/accept-invite?token=secure-token"
    assert invite_url in text_body
    assert "Jane &lt;Doe&gt;" in html_body
    assert "Acme &amp; Co" in html_body
    assert f'href="{invite_url}"' in html_body


def test_smtp_sender_returns_false_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_USER", "")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "configured")

    assert email._send_email_smtp("admin@example.com", "Subject", "<p>Body</p>", "Body") is False


def test_smtp_sender_returns_false_when_recipient_is_refused(monkeypatch):
    class RefusingSMTP:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def login(self, username, password):
            pass

        def sendmail(self, sender, recipients, message):
            return {recipients[0]: (550, b"Recipient rejected")}

    monkeypatch.setattr(settings, "SMTP_USER", "smtp-user")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "smtp-password")
    monkeypatch.setattr(settings, "SMTP_TLS", False)
    monkeypatch.setattr(
        email.smtplib,
        "SMTP",
        lambda host, port, timeout: RefusingSMTP(),
    )

    assert email._send_email_smtp("admin@example.com", "Subject", "<p>Body</p>", "Body") is False


def test_smtp_sender_uses_ssl_on_port_465_and_normalizes_app_password(monkeypatch):
    class AcceptingSMTP:
        def __init__(self):
            self.login_credentials = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def login(self, username, password):
            self.login_credentials = (username, password)

        def sendmail(self, sender, recipients, message):
            return {}

    smtp = AcceptingSMTP()
    monkeypatch.setattr(settings, "SMTP_USER", "smtp-user")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "app pass word")
    monkeypatch.setattr(settings, "SMTP_PORT", 465)
    monkeypatch.setattr(settings, "SMTP_TLS", True)
    monkeypatch.setattr(
        email.smtplib,
        "SMTP_SSL",
        lambda host, port, timeout, context: smtp,
    )

    assert email._send_email_smtp("admin@example.com", "Subject", "<p>Body</p>", "Body") is True
    assert smtp.login_credentials == ("smtp-user", "apppassword")


@pytest.mark.asyncio
async def test_send_invite_email_welcome_onboarding_template(monkeypatch):
    class RecordingSender:
        def __init__(self):
            self.message = None

        async def send_email(self, recipient, subject, text_body, html_body):
            self.message = (recipient, subject, text_body, html_body)
            return True

    sender = RecordingSender()
    monkeypatch.setattr(email, "get_email_sender", lambda: sender)

    sent = await email.send_invite_email(
        email="newuser@example.com",
        name="Meera Nair",
        token="inv-token-123",
        company_name="Acme Traders",
        inviter_name="Mohit Yadav",
        inviter_title="CEO, Acme Traders",
    )

    assert sent is True
    recipient, subject, text_body, html_body = sender.message
    assert recipient == "newuser@example.com"
    assert subject == "Welcome to Acme Traders! Let's get your account set up 🚀"
    assert "Hi Meera Nair," in text_body
    assert "Welcome aboard! We are thrilled to have Acme Traders partner with us." in text_body
    assert "quick onboarding checklist:" in text_body
    assert "Step 1: Log in to your secure workspace using your temporary credentials or set your password:" in text_body
    assert "Step 2: Explore our core features" in text_body
    assert "Step 3: Import your initial product catalog" in text_body
    assert "accept-invite?token=inv-token-123" in text_body
    assert "Mohit Yadav" in text_body
    assert "CEO, Acme Traders" in text_body
    assert "Welcome to Acme Traders! Let's get your account set up 🚀" in html_body
    assert "accept-invite?token=inv-token-123" in html_body
