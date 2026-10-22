from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class SecretError(ValueError):
    pass


def get_cipher():
    try:
        return Fernet(Path(settings.BAOTA_KEY_FILE).read_bytes().strip())
    except (OSError, ValueError, AttributeError) as exc:
        raise SecretError('面板密码加密密钥不可用，请联系管理员检查持久化密钥文件。') from exc


def encrypt_secret(value):
    return get_cipher().encrypt(value.encode('utf-8')).decode('ascii')


def decrypt_secret(value):
    try:
        return get_cipher().decrypt(value.encode('ascii')).decode('utf-8')
    except (InvalidToken, UnicodeError) as exc:
        raise SecretError('面板密码解密失败，请检查是否使用了原始加密密钥。') from exc
