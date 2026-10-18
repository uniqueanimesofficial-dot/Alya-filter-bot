import html
import re

# [Button text](buttonurl:https://example.com)        -> new row
# [Button text](buttonurl:https://example.com:same)   -> same row as previous button
BTN_RE = re.compile(r"\[(.+?)\]\(buttonurl:(.+?)(:same)?\)")
TAG_RE = re.compile(r"<[^>]+>")


def _plain(s: str) -> str:
    """Strip html tags (Telegram turns bare links into <a> tags) and unescape."""
    return html.unescape(TAG_RE.sub("", s)).strip()


def _valid_url(url: str) -> bool:
    return url.lower().startswith(("http://", "https://", "tg://"))


def parse_buttons(text: str):
    """Return (text_without_button_syntax, rows) where rows = [[{"text","url"}, ...], ...]."""
    text = text or ""
    rows = []
    for m in BTN_RE.finditer(text):
        label = _plain(m.group(1))
        url = _plain(m.group(2))
        if url.lower().startswith("t.me/"):
            url = "https://" + url
        if not label or not _valid_url(url):
            continue
        btn = {"text": label, "url": url}
        if m.group(3) and rows:
            rows[-1].append(btn)
        else:
            rows.append([btn])
    clean = BTN_RE.sub("", text).strip()
    return clean, rows


def split_keyword(rest: str):
    """Split '"multi word" body' or 'word body' into (keyword, body). Keyword is lowercased."""
    rest = (rest or "").strip()
    m = re.match(r'^(?:"|&quot;|“)(.+?)(?:"|&quot;|”)\s*(.*)$', rest, re.S)
    if m:
        kw, body = m.group(1), m.group(2)
    else:
        parts = rest.split(None, 1)
        if not parts:
            return "", ""
        kw = parts[0]
        body = parts[1] if len(parts) > 1 else ""
    return _plain(kw).lower(), body.strip()


def keyword_matches(keyword: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text, re.IGNORECASE) is not None
