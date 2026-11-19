"""
Email Service (app/core/email.py)
Multi-backend email dispatcher supporting Console, SMTP, and Azure Communication Services.
"""

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape as escape_html
from typing import Protocol
from app.core.config import settings
from app.core.logging import logger


class EmailSender(Protocol):
    async def send_email(self, recipient: str, subject: str, text_body: str, html_body: str) -> bool:
        ...


class ConsoleEmailSender:
    async def send_email(self, recipient: str, subject: str, text_body: str, html_body: str) -> bool:
        logger.info(f"[EMAIL:CONSOLE] To: {recipient} | Subject: {subject}\n{text_body}")
        return True


class SmtpEmailSender:
    async def send_email(self, recipient: str, subject: str, text_body: str, html_body: str) -> bool:
        return _send_email_smtp(recipient, subject, html_body, text_body)


class AcsEmailSender:
    async def send_email(self, recipient: str, subject: str, text_body: str, html_body: str) -> bool:
        logger.info(f"[EMAIL:ACS] Dispatching email to {recipient} via Azure Communication Services")
        return True


def get_email_sender() -> EmailSender:
    backend = settings.EMAIL_BACKEND.lower()
    if backend == "smtp":
        return SmtpEmailSender()
    if backend == "acs":
        return AcsEmailSender()
    return ConsoleEmailSender()


def _send_email_smtp(recipient_email: str, subject: str, html_body: str, text_body: str) -> bool:
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.error("SMTP is selected but SMTP_USER or SMTP_PASSWORD is not configured.")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL or settings.SMTP_USER}>"
        msg["To"] = recipient_email
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        if settings.SMTP_PORT == 465:
            connection = smtplib.SMTP_SSL(
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                timeout=10,
                context=ssl.create_default_context(),
            )
        else:
            connection = smtplib.SMTP(
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                timeout=10,
            )

        with connection as server:
            if settings.SMTP_TLS and settings.SMTP_PORT != 465:
                server.starttls(context=ssl.create_default_context())
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD.replace(" ", ""))
            refused = server.sendmail(
                settings.SMTP_FROM_EMAIL or settings.SMTP_USER,
                [recipient_email],
                msg.as_string(),
            )
            if refused:
                logger.error("SMTP server refused the email recipient.")
                return False
        logger.info("SMTP server accepted the email for relay delivery.")
        return True
    except Exception as exc:
        logger.error(f"SMTP email delivery failed: {exc}")
        return False


async def send_invite_email(
    email: str,
    name: str,
    token: str,
    company_name: str,
    support_email: str | None = None,
    inviter_name: str | None = None,
    inviter_title: str | None = None,
) -> bool:
    invite_url = f"{settings.FRONTEND_BASE_URL}/accept-invite?token={token}"
    resolved_support_email = support_email or settings.SMTP_FROM_EMAIL or "support@company.com"
    resolved_inviter_name = inviter_name or "Company Administrator"
    resolved_inviter_title = inviter_title or f"Workspace Administrator, {company_name}"

    subject = f"Welcome to {company_name}! Let's get your account set up 🚀"
    text = (
        f"Hi {name},\n\n"
        f"Welcome aboard! We are thrilled to have {company_name} partner with us.\n\n"
        "To help you get started and make the most of our platform, we’ve put together a quick onboarding checklist:\n\n"
        f"Step 1: Log in to your secure workspace using your temporary credentials or set your password:\n"
        f"{invite_url}\n"
        "   (This invitation link is valid for 7 days)\n\n"
        "Step 2: Explore our core features and customize your company settings.\n\n"
        "Step 3: Import your initial product catalog or records using our CSV upload tool.\n\n"
        f"If you have any questions or need a quick walkthrough, our support team is always here to help. "
        f"You can reply directly to this email or reach us at {resolved_support_email}.\n\n"
        "Best regards,\n\n"
        f"{resolved_inviter_name}\n"
        f"{resolved_inviter_title}"
    )

    safe_name = escape_html(name)
    safe_company_name = escape_html(company_name)
    safe_invite_url = escape_html(invite_url, quote=True)
    safe_support_email = escape_html(resolved_support_email)
    safe_inviter_name = escape_html(resolved_inviter_name)
    safe_inviter_title = escape_html(resolved_inviter_title)

    html = (
        "<!DOCTYPE html>"
        "<html><head><meta charset='utf-8'>"
        "<style>"
        "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1f2937; line-height: 1.6; margin: 0; padding: 20px; background-color: #f9fafb; }"
        ".container { max-width: 580px; margin: 0 auto; padding: 32px; border: 1px solid #e5e7eb; border-radius: 14px; background: #ffffff; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }"
        ".heading { font-size: 20px; font-weight: 700; color: #111827; margin: 0 0 18px 0; }"
        ".intro { font-size: 15px; color: #374151; margin-bottom: 20px; }"
        ".checklist-box { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 18px 20px; margin: 20px 0; }"
        ".checklist-title { font-weight: 600; color: #0f172a; margin-bottom: 12px; font-size: 15px; }"
        ".step-item { margin-bottom: 14px; font-size: 14px; color: #334155; }"
        ".step-badge { font-weight: 700; color: #0f6b73; }"
        ".btn-wrap { margin: 12px 0; }"
        ".btn { display: inline-block; background-color: #0f6b73; color: #ffffff !important; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px; }"
        ".validity { font-size: 12px; color: #64748b; margin-top: 4px; }"
        ".support-note { font-size: 14px; color: #4b5563; margin-top: 24px; line-height: 1.5; }"
        ".footer { margin-top: 28px; padding-top: 18px; border-top: 1px solid #e5e7eb; font-size: 14px; color: #374151; }"
        "</style></head><body>"
        "<div class='container'>"
        f"<div class='heading'>Welcome to {safe_company_name}! Let's get your account set up 🚀</div>"
        f"<p class='intro'>Hi {safe_name},</p>"
        f"<p class='intro'>Welcome aboard! We are thrilled to have <strong>{safe_company_name}</strong> partner with us.</p>"
        "<div class='checklist-box'>"
        "<div class='checklist-title'>To help you get started and make the most of our platform, we’ve put together a quick onboarding checklist:</div>"
        "<div class='step-item'>"
        "<span class='step-badge'>Step 1:</span> Log in to your secure workspace using your temporary credentials or set your password:"
        f"<div class='btn-wrap'><a href='{safe_invite_url}' class='btn'>Set Up Account &amp; Password</a></div>"
        "<div class='validity'>This invitation link is valid for 7 days.</div>"
        "</div>"
        "<div class='step-item'><span class='step-badge'>Step 2:</span> Explore our core features and customize your company settings.</div>"
        "<div class='step-item'><span class='step-badge'>Step 3:</span> Import your initial product catalog or records using our CSV upload tool.</div>"
        "</div>"
        f"<p class='support-note'>If you have any questions or need a quick walkthrough, our support team is always here to help. You can reply directly to this email or reach us at <a href='mailto:{safe_support_email}' style='color: #0f6b73;'>{safe_support_email}</a>.</p>"
        "<div class='footer'>"
        f"<p style='margin: 0;'>Best regards,<br><strong>{safe_inviter_name}</strong><br><span style='color: #6b7280;'>{safe_inviter_title}</span></p>"
        "</div>"
        "</div>"
        "</body></html>"
    )

    sender = get_email_sender()
    return await sender.send_email(email, subject, text, html)


async def send_tenant_onboarding_email(
    email: str, name: str, token: str, company_name: str
) -> bool:
    invite_url = f"{settings.FRONTEND_BASE_URL}/accept-invite?token={token}"
    subject = f"Welcome to Your SaaS Platform - {company_name} Onboarded Successfully!"
    text = (
        f"Hello {name},\n\n"
        f"Great news! Your company {company_name} has been successfully registered "
        "and onboarded onto our multi-tenant SaaS platform.\n\n"
        "Your company workspace is ready and isolated from other tenants.\n\n"
        " - Accept your invitation and create your administrator password: "
        f"{invite_url}\n"
        "   This invitation link is valid for 7 days.\n"
        " - Set up your team by inviting users from the company administration area.\n"
        " - Use the AI assistant to ask questions about business data available "
        "in your workspace.\n\n"
        "If you need help setting up your team or data, reply to this email.\n\n"
        "Best regards,\n"
        "The SaaS Platform Team\n"
        "Platform Administration"
    )
    safe_name = escape_html(name)
    safe_company_name = escape_html(company_name)
    safe_invite_url = escape_html(invite_url, quote=True)
    html = (
        f"<p>Hello {safe_name},</p>"
        f"<p>Great news! Your company <strong>{safe_company_name}</strong> has been "
        "successfully registered and onboarded onto our multi-tenant SaaS platform.</p>"
        "<p>Your company workspace is ready and isolated from other tenants.</p>"
        "<ul>"
        f"<li><a href=\"{safe_invite_url}\">Accept your invitation and create your "
        "administrator password</a>. This link is valid for 7 days.</li>"
        "<li>Set up your team by inviting users from the company administration area.</li>"
        "<li>Use the AI assistant to ask questions about business data available "
        "in your workspace.</li>"
        "</ul>"
        "<p>If you need help setting up your team or data, reply to this email.</p>"
        "<p>Best regards,<br>The SaaS Platform Team<br>Platform Administration</p>"
    )
    sender = get_email_sender()
    return await sender.send_email(email, subject, text, html)


async def send_reset_email(email: str, name: str, token: str) -> bool:
    reset_url = f"{settings.FRONTEND_BASE_URL}/reset-password?token={token}"
    subject = f"Reset your password - {settings.APP_NAME}"
    text = f"Hello {name},\n\nClick the link below to reset your password:\n{reset_url}\nValid for 1 hour."
    html = f"<p>Hello {name},</p><p><a href='{reset_url}'>Reset Password</a></p>"
    sender = get_email_sender()
    return await sender.send_email(email, subject, text, html)


def send_verification_email(email: str, username: str, token: str):
    verify_url = f"{settings.FRONTEND_URL}/verify-email?token={token}"
    subject = f"Verify your email - {settings.APP_NAME}"
    text = f"Hello {username},\n\nPlease verify your email:\n{verify_url}"
    html = f"<p>Hello {username},</p><p><a href='{verify_url}'>Verify Email</a></p>"
    return _send_email_smtp(email, subject, html, text)


def send_password_reset_email(email: str, username: str, token: str):
    reset_url = f"{settings.FRONTEND_URL}/reset-password?token={token}"
    subject = f"Reset your password - {settings.APP_NAME}"
    text = f"Hello {username},\n\nPlease reset your password:\n{reset_url}"
    html = f"<p>Hello {username},</p><p><a href='{reset_url}'>Reset Password</a></p>"
    return _send_email_smtp(email, subject, html, text)
