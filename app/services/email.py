import os, smtplib
from email.message import EmailMessage

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER or "no-reply@edumath.local")

def send_email_code(to_email: str, code: str, purpose: str):
    subject = f"[EduMath] Código {purpose}"
    body = f"Tu código de {purpose} es: {code}\nNo compartas este código con nadie."
    # Si no hay SMTP configurado, modo demo en consola
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        print(f"[DEV EMAIL] To:{to_email} | {subject} | {body}")
        return

    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
