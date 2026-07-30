"""密码哈希工具测试"""

from __future__ import annotations

from lumio.shared.password import hash_password, verify_password


def test_hash_password_format():
    """哈希输出为 4 段 pbkdf2_sha256 格式"""
    encoded = hash_password("s3cr3t-pass")
    parts = encoded.split("$")
    assert len(parts) == 4
    assert parts[0] == "pbkdf2_sha256"
    assert parts[1] == "600000"
    assert parts[2] and parts[3]


def test_hash_password_uses_random_salt():
    """相同密码两次哈希不同（随机盐）"""
    assert hash_password("same") != hash_password("same")


def test_verify_correct_password():
    encoded = hash_password("correct-horse")
    assert verify_password("correct-horse", encoded) is True


def test_verify_wrong_password():
    encoded = hash_password("correct-horse")
    assert verify_password("wrong-horse", encoded) is False


def test_verify_malformed_hash():
    assert verify_password("anything", "not-a-valid-hash") is False
    assert verify_password("anything", "") is False


def test_verify_unknown_algorithm():
    assert verify_password("pw", "md5$1$salt$digest") is False


def test_verify_unicode_password():
    encoded = hash_password("密码测试🔒")
    assert verify_password("密码测试🔒", encoded) is True
    assert verify_password("密码测试", encoded) is False
