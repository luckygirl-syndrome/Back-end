from sdk.coolsms import Coolsms
from app.core.config import settings


def send_sms(to: str, text: str) -> None:
    api = Coolsms(settings.COOLSMS_API_KEY, settings.COOLSMS_API_SECRET)
    api.set_api_config("sms", "2")
    api.request_post("send", {
        "to": to,
        "from": settings.COOLSMS_FROM,
        "text": text,
        "type": "SMS",
    })


def send_verification_code(to: str, code: str) -> None:
    send_sms(to, f"[또바바] 인증번호 {code}를 입력해주세요. (3분 내 유효)")
