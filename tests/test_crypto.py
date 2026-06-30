from __future__ import annotations

import unittest

from cryptography.fernet import Fernet, InvalidToken

from kinozal_scraper.crypto import decrypt_bytes, encrypt_bytes


class TestCryptoRoundTrip(unittest.TestCase):
    def test_encrypt_decrypt_round_trip_small_payload(self) -> None:
        key = Fernet.generate_key()
        data = b"hello world"
        self.assertEqual(decrypt_bytes(encrypt_bytes(data, key), key), data)

    def test_encrypt_decrypt_round_trip_empty_payload(self) -> None:
        key = Fernet.generate_key()
        self.assertEqual(decrypt_bytes(encrypt_bytes(b"", key), key), b"")

    def test_encrypt_decrypt_round_trip_binary_payload(self) -> None:
        # Mimic an anon.session blob — arbitrary binary, including null bytes.
        key = Fernet.generate_key()
        data = bytes(range(256)) * 16
        self.assertEqual(decrypt_bytes(encrypt_bytes(data, key), key), data)

    def test_decrypt_with_wrong_key_raises(self) -> None:
        key_a = Fernet.generate_key()
        key_b = Fernet.generate_key()
        ciphertext = encrypt_bytes(b"top secret", key_a)
        with self.assertRaises(InvalidToken):
            decrypt_bytes(ciphertext, key_b)

    def test_encrypt_is_non_deterministic(self) -> None:
        # Fernet uses a random IV per call — same input, different ciphertext.
        key = Fernet.generate_key()
        a = encrypt_bytes(b"same data", key)
        b = encrypt_bytes(b"same data", key)
        self.assertNotEqual(a, b)
        # But both decrypt back to the same plaintext.
        self.assertEqual(decrypt_bytes(a, key), decrypt_bytes(b, key))


if __name__ == "__main__":
    unittest.main()
