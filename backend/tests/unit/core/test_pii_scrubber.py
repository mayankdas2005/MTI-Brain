"""Unit tests for core/pii_scrubber — PII regex detection/masking."""

import pytest

from app.core.pii_scrubber import scrub


class TestScrubEmails:
    def test_simple_email(self):
        assert "[EMAIL]" in scrub("Contact me at user@example.com")

    def test_email_with_dots(self):
        assert "[EMAIL]" in scrub("john.doe.jr@company.co.uk")

    def test_email_with_plus(self):
        assert "[EMAIL]" in scrub("user+tag@gmail.com")

    def test_multiple_emails(self):
        result = scrub("From a@b.com to c@d.org")
        assert result.count("[EMAIL]") == 2


class TestScrubPhones:
    def test_dashed_phone(self):
        assert "[PHONE]" in scrub("Call 555-123-4567")

    def test_dotted_phone(self):
        assert "[PHONE]" in scrub("Call 555.123.4567")

    def test_plain_phone(self):
        assert "[PHONE]" in scrub("Call 5551234567")


class TestScrubSSNs:
    def test_standard_ssn(self):
        assert "[SSN]" in scrub("SSN: 123-45-6789")

    def test_ssn_in_context(self):
        result = scrub("My social is 999-88-7777, thanks")
        assert "[SSN]" in result
        assert "999-88-7777" not in result


class TestScrubCreditCards:
    def test_dashed_card(self):
        assert "[CARD]" in scrub("Card: 4111-1111-1111-1111")

    def test_spaced_card(self):
        assert "[CARD]" in scrub("Card: 4111 1111 1111 1111")

    def test_plain_card(self):
        assert "[CARD]" in scrub("Card: 4111111111111111")


class TestNoFalsePositives:
    def test_normal_text(self):
        text = "What was total revenue last quarter?"
        assert scrub(text) == text

    def test_dates_not_scrubbed(self):
        text = "Between 2024-01-15 and 2024-03-31"
        assert scrub(text) == text

    def test_short_numbers_not_scrubbed(self):
        text = "Order ID 12345"
        assert scrub(text) == text

    def test_empty_string(self):
        assert scrub("") == ""
