"""Indian contact normalization.

Encodes the Phase-0 spike gotcha (CHALLENGES.md #4): Razorpay rejects phone numbers with
long recurring-digit runs ("Recurring digits in customer contact are disallowed").
Razorpay's exact threshold is undocumented; we conservatively reject runs of >= 5 identical
digits so fixtures and demo data never trip the real API.
"""

import re


class ContactError(ValueError):
    """Raised when a contact number cannot be normalized to a valid Indian MSISDN."""


_RECURRING_RUN = re.compile(r"(\d)\1{4,}")  # any digit repeated 5+ times consecutively
_INDIAN_MSISDN = re.compile(r"^\+91\d{10}$")


def normalize_indian_contact(raw: str) -> str:
    """Normalize to E.164-ish ``+91XXXXXXXXXX``; raise ContactError if invalid/spammy."""
    cleaned = re.sub(r"[\s\-().]", "", raw.strip())

    if not cleaned.startswith("+91"):
        raise ContactError("contact must be an Indian number starting with +91")
    if not _INDIAN_MSISDN.match(cleaned):
        raise ContactError("contact must have +91 followed by exactly 10 digits")
    if _RECURRING_RUN.search(cleaned):
        raise ContactError("recurring digit run detected — looks spammy to Razorpay")

    return cleaned
