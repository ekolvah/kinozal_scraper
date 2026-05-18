from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet


def encrypt_bytes(data: bytes, key: bytes) -> bytes:
    """Encrypt arbitrary bytes with a Fernet key. Pure — no file IO."""
    return Fernet(key).encrypt(data)


def decrypt_bytes(data: bytes, key: bytes) -> bytes:
    """Decrypt Fernet-ciphertext with the matching key. Pure — no file IO.

    Raises `cryptography.fernet.InvalidToken` if the key does not match the
    ciphertext (the caller decides whether to log / re-raise).
    """
    return Fernet(key).decrypt(data)


class crypto:
    """Backwards-compatibility namespace for the existing call-sites in
    `TelegramChannelSummarizer.py`. The pure helpers above are the
    preferred surface for new code and tests.
    """

    @staticmethod
    def save_encrypter_session() -> None:
        """Generate a Fernet key, write it to `secret.key`, then encrypt
        `anon.session` into `anon.session.encrypted`. Used once during
        operator setup; not on the cron hot path.
        """
        key = Fernet.generate_key()
        Path("secret.key").write_bytes(key)
        session_data = Path("anon.session").read_bytes()
        Path("anon.session.encrypted").write_bytes(encrypt_bytes(session_data, key))

    @staticmethod
    def load_encrypter_session() -> None:
        """Decrypt `anon.session.encrypted` into `anon.session` on the
        local filesystem so Telethon can pick it up. Runs at the start of
        every cron invocation (called from the `__main__` block in
        `telegram_summarizer.py`).
        """
        key = os.environ["SECRET_KEY"].encode()
        encrypted = Path("anon.session.encrypted").read_bytes()
        Path("anon.session").write_bytes(decrypt_bytes(encrypted, key))
