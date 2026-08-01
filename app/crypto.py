"""הצפנה סימטרית לגיבוי שיוצא החוצה.

ה-ROADMAP הציע `age` או `gpg`. שניהם טובים, ושניהם בינאריים חיצוניים
שאינם מובטחים ב-image של Render — וכישלון שם היה שקט: הגיבוי השבועי
פשוט לא יוצא, ואף אחד לא מגלה עד שצריך אותו. לכן ההצפנה נעשית בתהליך.

הפורמט: AES-256-GCM, עם מפתח שנגזר מסיסמה ב-scrypt. GCM ולא CBC כי הוא
מאמת: קובץ שהשתנה בדרך נכשל בפענוח במקום להתפענח לזבל.
"""

from __future__ import annotations

import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# חתימה שמזהה את הפורמט, כדי שקובץ מגרסה אחרת ייכשל ברעש ולא בשקט.
MAGIC = b"MDBK1\x00"

SALT_BYTES = 16
NONCE_BYTES = 12
KEY_BYTES = 32

# פרמטרי scrypt. n=2**15 לוקח כמה עשיות שנייה — יקר מספיק כדי שניחוש
# סיסמאות יהיה איטי, זול מספיק לגיבוי שרץ פעם בשבוע.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1


class CryptoError(Exception):
    """פענוח שנכשל. לא נושא פרטים — הם לא עוזרים למי שמנסה לנחש."""


def _derive(passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=KEY_BYTES, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt(data: bytes, passphrase: str) -> bytes:
    """מצפין, ומחזיר מבנה שנושא את כל מה שצריך כדי לפענח — חוץ מהמפתח.

    ה-salt וה-nonce אינם סודיים ונשמרים בגלוי בתוך הקובץ. מה שסודי הוא
    הסיסמה בלבד, והיא לא נמצאת כאן ולא נשלחת יחד עם הקובץ.
    """
    if not passphrase:
        raise CryptoError("לא הוגדרה סיסמת הצפנה")

    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    key = _derive(passphrase, salt)

    # ה-header נכנס כ-associated data: מי שישנה אותו יפיל את האימות
    # במקום לגרום לפענוח שקט של תוכן שגוי.
    header = MAGIC + struct.pack("!III", SCRYPT_N, SCRYPT_R, SCRYPT_P) + salt + nonce
    sealed = AESGCM(key).encrypt(nonce, data, header)
    return header + sealed


def decrypt(blob: bytes, passphrase: str) -> bytes:
    """מפענח. כל תקלה — פורמט, סיסמה, שינוי בקובץ — היא CryptoError."""
    header_len = len(MAGIC) + 12 + SALT_BYTES + NONCE_BYTES
    if len(blob) < header_len or not blob.startswith(MAGIC):
        raise CryptoError("הקובץ אינו בפורמט הצפוי")

    offset = len(MAGIC)
    n, r, p = struct.unpack("!III", blob[offset : offset + 12])
    offset += 12
    salt = blob[offset : offset + SALT_BYTES]
    offset += SALT_BYTES
    nonce = blob[offset : offset + NONCE_BYTES]
    offset += NONCE_BYTES

    header = blob[:header_len]
    kdf = Scrypt(salt=salt, length=KEY_BYTES, n=n, r=r, p=p)
    key = kdf.derive(passphrase.encode("utf-8"))

    try:
        return AESGCM(key).decrypt(nonce, blob[header_len:], header)
    except Exception as exc:
        raise CryptoError("הפענוח נכשל") from exc
