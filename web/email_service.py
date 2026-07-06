"""Email via Mailjet. Falls back to console logging when unconfigured (dev).

Adapted from an earlier internal project.
"""
import os
from mailjet_rest import Client

MAILJET_API_KEY = os.getenv("MAILJET_API_KEY", "")
MAILJET_SECRET_KEY = os.getenv("MAILJET_SECRET_KEY", "")
FROM_EMAIL = os.getenv("MAILJET_FROM_EMAIL", "noreply@example.com")
FROM_NAME = os.getenv("MAILJET_FROM_NAME", "Workflow Builder")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8000")


def _client() -> Client | None:
    if not MAILJET_API_KEY or not MAILJET_SECRET_KEY:
        return None
    return Client(auth=(MAILJET_API_KEY, MAILJET_SECRET_KEY), version="v3.1")


def _send(to_email: str, to_name: str, subject: str, link: str, body: str) -> bool:
    mj = _client()
    if mj is None:
        print(f"[EMAIL] Mailjet not configured. {subject}: {link}")
        return True
    html = (
        f'<div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:32px">'
        f"<h2>{subject}</h2><p>{body}</p>"
        f'<p><a href="{link}" style="background:#6200EE;color:#fff;padding:12px 28px;'
        f'border-radius:6px;text-decoration:none;display:inline-block">Continue</a></p>'
        f'<p style="color:#999;font-size:13px">Or paste this link: {link}<br>Expires in 24 hours.</p></div>'
    )
    res = mj.send.create(data={"Messages": [{
        "From": {"Email": FROM_EMAIL, "Name": FROM_NAME},
        "To": [{"Email": to_email, "Name": to_name}],
        "Subject": subject,
        "TextPart": f"{body}\n\n{link}\n\nExpires in 24 hours.",
        "HTMLPart": html,
    }]})
    if res.status_code == 200:
        return True
    print(f"[EMAIL] send failed: {res.status_code} - {res.json()}")
    return False


def send_verification_email(to_email: str, to_name: str, token: str) -> bool:
    link = f"{FRONTEND_URL}/verify-email?token={token}"
    return _send(to_email, to_name, "Confirm your email address", link,
                 "Welcome! Please confirm your email address to activate your account.")


def send_password_reset_email(to_email: str, token: str) -> bool:
    link = f"{FRONTEND_URL}/reset-password?token={token}"
    return _send(to_email, to_email.split("@")[0], "Reset your password", link,
                 "You requested a password reset. Click below to set a new password.")
