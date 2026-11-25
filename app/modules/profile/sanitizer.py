"""
Profile Sanitizer (app/modules/profile/sanitizer.py)
Pure normalization and validation functions for progressive user profile inputs.
Never imports motor, redis, google-genai, or fastapi.
"""

import re
import unicodedata
from typing import List, Optional

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
AADHAAR_REGEX = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")
PAN_REGEX = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", re.IGNORECASE)


def normalize_text(text: str) -> str:
    """NFKC normalize, strip control characters, collapse spaces, and trim."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKC", str(text))
    # Remove unicode control characters
    chars = [c for c in norm if not unicodedata.category(c).startswith("C")]
    cleaned = "".join(chars)
    # Collapse whitespace
    return re.sub(r"\s+", " ", cleaned).strip()


def contains_suspicious_patterns(text: str) -> bool:
    """Reject values containing injection tokens, markup, URLs, email, phone, Aadhaar, PAN."""
    # Forbidden substrings
    for bad in ["@", "://", "www.", "<", ">", "{", "}"]:
        if bad in text:
            return True

    # Check for email
    if EMAIL_REGEX.search(text):
        return True

    # Check for 7 or more digits (phone numbers)
    digit_count = sum(1 for c in text if c.isdigit())
    if digit_count >= 7:
        return True

    # Check for Aadhaar (12 digits)
    if AADHAAR_REGEX.search(text):
        return True

    # Check for Indian PAN card pattern
    if PAN_REGEX.search(text):
        return True

    return False


def sanitize_name(val: Optional[str]) -> Optional[str]:
    """
    Sanitize and validate a user's display name.
    2-80 chars, letters (any script), spaces, and . ' - only. No digits.
    """
    if not val:
        return None
    cleaned = normalize_text(val)
    if len(cleaned) < 2 or len(cleaned) > 80:
        return None
    if contains_suspicious_patterns(cleaned):
        return None

    # Allowed: Letters (any script), spaces, period, apostrophe, hyphen
    for char in cleaned:
        cat = unicodedata.category(char)
        if not (cat.startswith("L") or char in (" ", ".", "'", "-")):
            return None
    return cleaned


def sanitize_company_name(val: Optional[str]) -> Optional[str]:
    """
    Sanitize and validate a company name.
    2-120 chars, letters, digits, spaces and & . , ' - ( ) only.
    """
    if not val:
        return None
    cleaned = normalize_text(val)
    if len(cleaned) < 2 or len(cleaned) > 120:
        return None
    if contains_suspicious_patterns(cleaned):
        return None

    # Allowed: Letters, digits, spaces, and punctuation: & . , ' - ( )
    allowed_punct = {" ", "&", ".", ",", "'", "-", "(", ")"}
    for char in cleaned:
        cat = unicodedata.category(char)
        if not (cat.startswith("L") or cat.startswith("N") or char in allowed_punct):
            return None
    return cleaned


def sanitize_interests(items: Optional[List[str]]) -> List[str]:
    """
    Sanitize list of interest topics.
    Up to 5 items, each 2-60 chars, de-duplicated case-insensitively.
    """
    if not items:
        return []
    result: List[str] = []
    seen = set()
    for item in items:
        if not item:
            continue
        cleaned = normalize_text(item)
        if len(cleaned) < 2 or len(cleaned) > 60:
            continue
        if contains_suspicious_patterns(cleaned):
            continue
        lower_key = cleaned.lower()
        if lower_key not in seen:
            seen.add(lower_key)
            result.append(cleaned)
        if len(result) >= 5:
            break
    return result


def matches_phrase(message: str, phrases: List[str]) -> bool:
    """Case-insensitive matching for whole-message or leading phrase."""
    if not message or not phrases:
        return False
    msg_norm = normalize_text(message).lower()
    for phrase in phrases:
        p_norm = normalize_text(phrase).lower()
        if not p_norm:
            continue
        if msg_norm == p_norm:
            return True
        # Leading phrase check (e.g. "skip please", "not now thanks")
        if msg_norm.startswith(p_norm + " ") or msg_norm.startswith(p_norm + ","):
            return True
    return False
