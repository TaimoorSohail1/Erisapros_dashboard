"""Keep the FT Williams credential out of stored and displayed XML previews."""

from html import escape
import re


_KEY_ELEMENT = re.compile(
    r"(<(?:[\w.-]+:)?KeyID\b[^>]*>).*?(</(?:[\w.-]+:)?KeyID\s*>)",
    re.IGNORECASE | re.DOTALL,
)


def replace_ftw_xml_key(xml: str, key: str = "***") -> str:
    safe_key = escape(key)
    return _KEY_ELEMENT.sub(lambda match: f"{match[1]}{safe_key}{match[2]}", xml)
