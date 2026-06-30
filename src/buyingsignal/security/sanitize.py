"""Defensive sanitization of untrusted external text before it reaches a prompt.

Collected text comes from third-party feeds and pages we do not control, so it
is a prompt-injection vector. This module:

  * strips control characters and zero-width / bidi tricks,
  * caps length to bound token cost and blast radius,
  * defangs common injection phrases so they cannot act as instructions,
  * wraps the result in an explicit delimiter the prompt treats as *data only*.

This is mitigation, not a guarantee; the scoring prompt also instructs the model
to treat the delimited block strictly as data to classify.
"""

from __future__ import annotations

import re
import unicodedata

# Hard cap on characters fed to the model from any single external field.
MAX_CHARS = 8000

# A stable delimiter the scoring prompt references when describing untrusted data.
DATA_OPEN = "<<<UNTRUSTED_SIGNAL_DATA>>>"
DATA_CLOSE = "<<<END_UNTRUSTED_SIGNAL_DATA>>>"

# Zero-width, bidi-override and other invisible characters used to smuggle text.
_INVISIBLE_RE = re.compile(
    "[​‌‍‎‏‪‫‬‭‮⁠﻿]"
)

# Phrases that try to escape the data frame and issue instructions. We neutralize
# them by inserting a zero-width-free marker so they lose their imperative form
# while keeping the text human-readable for reviewers.
_INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+instructions"),
    re.compile(r"(?i)disregard\s+(all\s+)?(previous|prior|above)"),
    re.compile(r"(?i)you\s+are\s+now\b"),
    re.compile(r"(?i)system\s*:"),
    re.compile(r"(?i)assistant\s*:"),
    re.compile(r"(?i)<\s*/?\s*(system|assistant|user)\s*>"),
    re.compile(r"(?i)new\s+instructions?\s*:"),
    re.compile(r"(?i)respond\s+only\s+with"),
]


def _strip_control(text: str) -> str:
    # Keep printable + common whitespace; drop other C0/C1 control chars.
    out = []
    for ch in text:
        if ch in ("\n", "\t", " "):
            out.append(ch)
            continue
        if unicodedata.category(ch).startswith("C"):
            continue
        out.append(ch)
    return "".join(out)


def sanitize_text(text: str | None, *, max_chars: int = MAX_CHARS) -> str:
    """Return external text cleaned of control/injection content and length-capped."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE_RE.sub("", text)
    text = _strip_control(text)
    for pat in _INJECTION_PATTERNS:
        text = pat.sub("[redacted-instruction]", text)
    # Collapse runaway whitespace, then cap length.
    text = re.sub(r"[ \t]{3,}", "  ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + " …[truncated]"
    return text


def wrap_as_data(text: str) -> str:
    """Frame sanitized text as an inert data block for inclusion in a prompt."""
    return f"{DATA_OPEN}\n{text}\n{DATA_CLOSE}"
