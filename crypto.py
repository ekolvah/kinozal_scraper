from cryptography.fernet import Fernet
import os

class crypto:
    @staticmethod
    def save_encrypter_session():
        # Генерация ключа шифрования
        key = Fernet.generate_key()

        # Сохранение ключа шифрования в файл
        with open('secret.key', 'wb') as key_file:
            key_file.write(key)

        # Загрузка ключа шифрования из файла
        with open('secret.key', 'rb') as key_file:
            key = key_file.read()

        # Инициализация объекта шифрования
        cipher_suite = Fernet(key)

        # Шифрование файла сессии
        with open('anon.session', 'rb') as file_to_encrypt:
            file_data = file_to_encrypt.read()
        encrypted_data = cipher_suite.encrypt(file_data)

        # Сохранение зашифрованного файла
        with open('anon.session.encrypted', 'wb') as encrypted_file:
            encrypted_file.write(encrypted_data)

    @staticmethod
    def load_encrypter_session():
        # Load the encryption key from the environment variable
        key = os.environ['SECRET_KEY'].encode()

        # Initialize the Fernet object with the loaded key
        cipher_suite = Fernet(key)

        # Open the encrypted file and read its content
        with open('anon.session.encrypted', 'rb') as encrypted_file:
            encrypted_data = encrypted_file.read()

        # Decrypt the read content
        decrypted_data = cipher_suite.decrypt(encrypted_data)

        # Open the session file in write-binary mode and write the decrypted data to it
        with open('anon.session', 'wb') as session_file:
            session_file.write(decrypted_data)

