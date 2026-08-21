"""Contact validation — encodes the Phase-0 gotcha: Razorpay rejects spammy-looking numbers."""

import pytest

from paypilot.domain.contacts import ContactError, normalize_indian_contact


def test_accepts_realistic_contact() -> None:
    assert normalize_indian_contact("+919876543210") == "+919876543210"


def test_normalizes_spaces_and_dashes() -> None:
    assert normalize_indian_contact("+91 98765-43210") == "+919876543210"


def test_rejects_recurring_digits_like_razorpay() -> None:
    with pytest.raises(ContactError, match="recurring"):
        normalize_indian_contact("+919999999999")


def test_rejects_wrong_digit_count() -> None:
    with pytest.raises(ContactError, match="10 digits"):
        normalize_indian_contact("+9112345")


def test_rejects_non_indian_prefix() -> None:
    with pytest.raises(ContactError, match="\\+91"):
        normalize_indian_contact("+447911123456")
