"""
Masar Core — تشفير أسرار البريد (B4).

كلمة مرور تطبيق Gmail (App Password) لكل صندوق بريد مخصّص لعميل تُخزَّن
مشفّرة بعمود `mail_links.secret_enc` (نص Fernet متماثل، مكتبة cryptography)
لا بنص صريح أبدًا — المفتاح `MAIL_FERNET_KEY` يُقرأ من بيئة الخادم فقط
(.env، يُولَّد مرة واحدة عبر deploy/autodeploy.sh — راجع تعليقه هناك) ولا
يُمرَّر أبدًا عبر أي جلسة Claude ولا يُطبع بأي سجلّ.

فشل مغلق (fail-closed) عمدًا: غياب MAIL_FERNET_KEY بالبيئة يمنع أي تشفير/
فك تشفير بدل السماح بتخزين نص صريح بالخطأ.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


class MailCryptoUnavailable(RuntimeError):
    """MAIL_FERNET_KEY غير معرَّف بالبيئة — لا يمكن تشفير/فك تشفير أي سرّ."""


def _fernet() -> Fernet:
    key = os.environ.get("MAIL_FERNET_KEY", "")
    if not key:
        raise MailCryptoUnavailable("MAIL_FERNET_KEY غير معرَّف على الخادم — لا يمكن التعامل مع أسرار البريد")
    try:
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise MailCryptoUnavailable(f"MAIL_FERNET_KEY غير صالح: {exc}") from exc


def encrypt_secret(plain: str) -> str:
    """يشفّر نصًا صريحًا (كلمة مرور تطبيق) ويرجع توكن Fernet (نص ASCII آمن
    للتخزين بعمود نصي). يرفع MailCryptoUnavailable إن غاب المفتاح."""
    if plain is None:
        raise ValueError("plain مطلوب")
    token = _fernet().encrypt(plain.encode("utf-8"))
    return token.decode("ascii")


def decrypt_secret(token: str) -> str:
    """يفك تشفير توكن Fernet مخزّن ويرجع النص الصريح. يرفع MailCryptoUnavailable
    إن غاب المفتاح، أو ValueError إن كان التوكن تالفًا/غير صالح (مفتاح تغيّر،
    أو بيانات تالفة) — لا نُخفي هذا الخطأ لأنه يعني عدم القدرة على الإرسال."""
    if not token:
        raise ValueError("token مطلوب")
    try:
        plain = _fernet().decrypt(token.encode("ascii") if isinstance(token, str) else token)
    except InvalidToken as exc:
        raise ValueError("توكن سرّ البريد غير صالح أو تالف (مفتاح مختلف؟)") from exc
    return plain.decode("utf-8")
