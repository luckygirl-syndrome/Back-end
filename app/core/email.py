import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.core.config import settings


def send_email(to: str, subject: str, body_html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.EMAIL_FROM or settings.SMTP_USER
    msg["To"] = to
    msg.attach(MIMEText(body_html, "html"))

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.sendmail(msg["From"], to, msg.as_string())


def send_signup_verification_code(to: str, code: str) -> None:
    subject = "[또바바] 이메일 인증 코드"
    body = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;">
      <h2>이메일 인증</h2>
      <p>아래 6자리 인증 코드를 입력해 주세요. 코드는 <strong>10분</strong> 후 만료됩니다.</p>
      <div style="font-size:32px;font-weight:bold;letter-spacing:8px;padding:16px 0;">{code}</div>
      <p style="color:#888;font-size:12px;">본인이 요청하지 않았다면 이 이메일을 무시하세요.</p>
    </div>
    """
    send_email(to, subject, body)


def send_password_reset_code(to: str, code: str) -> None:
    subject = "[또바바] 비밀번호 재설정 인증 코드"
    body = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;">
      <h2>비밀번호 재설정</h2>
      <p>아래 6자리 인증 코드를 입력해 주세요. 코드는 <strong>10분</strong> 후 만료됩니다.</p>
      <div style="font-size:32px;font-weight:bold;letter-spacing:8px;padding:16px 0;">{code}</div>
      <p style="color:#888;font-size:12px;">본인이 요청하지 않았다면 이 이메일을 무시하세요.</p>
    </div>
    """
    send_email(to, subject, body)
