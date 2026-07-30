"""密码哈希工具

使用 PBKDF2-HMAC-SHA256（Python 标准库 hashlib 实现），格式兼容 Django：
    pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>

选择 PBKDF2 而非 bcrypt/argon2 的理由：
- 零外部依赖（passlib + bcrypt 在某些 musl/alpine 镜像编译困难）
- Django/Flask 默认算法，安全性与生态成熟
- 600000 次迭代满足 OWASP 2023 建议
"""

from __future__ import annotations

import base64
import hashlib
import secrets

_ITERATIONS = 600000
_ALGORITHM = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    """哈希密码，返回标准格式字符串"""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGORITHM}${_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    """验证密码是否匹配哈希

    使用恒定时间比较防止时序攻击。
    """
    try:
        algorithm, iterations, salt_b64, hash_b64 = encoded.split("$")
        if algorithm != _ALGORITHM:
            return False
        iterations_int = int(iterations)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations_int)
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False
