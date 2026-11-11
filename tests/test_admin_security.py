"""Regression checks for the lottery admin authorization gate."""

import unittest

from admin_security import (
    pin_fingerprint,
    pin_is_configured,
    session_is_authorized,
    verify_admin_pin,
)


class AdminSecurityTests(unittest.TestCase):
    def setUp(self):
        self.valid_pin = "G7!qV4@b5K9#pR2"

    def test_no_pin_fails_closed(self):
        self.assertFalse(pin_is_configured(None))
        self.assertFalse(verify_admin_pin("", None))
        self.assertFalse(session_is_authorized(None, None))

    def test_weak_or_default_pins_rejected(self):
        for value in ("1688", "123456789012", "aaaaaaaaaaaa", "admin123456789", " TestValue123! "):
            with self.subTest(value=value):
                self.assertFalse(pin_is_configured(value))
                self.assertFalse(verify_admin_pin(value, value))

    def test_correct_pin_is_accepted(self):
        self.assertTrue(pin_is_configured(self.valid_pin))
        self.assertTrue(verify_admin_pin(self.valid_pin, self.valid_pin))

    def test_wrong_pin_or_stale_session_is_rejected(self):
        self.assertFalse(verify_admin_pin(self.valid_pin + "x", self.valid_pin))
        self.assertFalse(session_is_authorized(pin_fingerprint(self.valid_pin), "Q9!jL3@e6M8#zR4"))
        self.assertTrue(session_is_authorized(pin_fingerprint(self.valid_pin), self.valid_pin))

    def test_no_auth_without_config_even_with_fingerprint(self):
        self.assertFalse(session_is_authorized(pin_fingerprint(self.valid_pin), "1688"))


if __name__ == "__main__":
    unittest.main()
