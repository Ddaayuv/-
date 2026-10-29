import re
import unicodedata
from urllib.parse import urlparse

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").translate(ARABIC_DIGITS).strip().lower()
    value = re.sub(r"[إأآٱ]", "ا", value)
    value = re.sub(r"ى", "ي", value)
    value = re.sub(r"ة", "ه", value)
    value = re.sub(r"[^\w\s-]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()

def key(value: str) -> str:
    return normalize(value).replace(" ", "")

def is_url(value: str) -> bool:
    try:
        return urlparse(value).scheme in {"http", "https"} and bool(urlparse(value).netloc)
    except Exception:
        return False

def esc(value: str) -> str:
    return (value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def parse_chat_ref(raw: str):
    """Turn @username, t.me/username, or a numeric id into something get_chat() accepts."""
    raw = (raw or "").strip()
    m = re.search(r"(?:https?://)?t\.me/([A-Za-z][A-Za-z0-9_]{3,})", raw)
    if m:
        return "@" + m.group(1)
    if re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,}", raw):
        return raw
    if re.fullmatch(r"-?\d{5,}", raw):
        return int(raw)
    return None
