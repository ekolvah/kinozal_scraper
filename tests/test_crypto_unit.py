from cryptography.fernet import Fernet, InvalidToken

import pytest


def test_fernet_roundtrip() -> None:
    key = Fernet.generate_key()
    data = b"arbitrary session bytes 12345"

    cipher = Fernet(key)
    decrypted = Fernet(key).decrypt(cipher.encrypt(data))

    assert decrypted == data


def test_fernet_wrong_key_raises() -> None:
    data = b"secret data"
    encrypted = Fernet(Fernet.generate_key()).encrypt(data)

    with pytest.raises(InvalidToken):
        Fernet(Fernet.generate_key()).decrypt(encrypted)


def test_fernet_key_is_bytes() -> None:
    key = Fernet.generate_key()
    assert isinstance(key, bytes)
    assert len(key) == 44  # base64-encoded 32-byte key
