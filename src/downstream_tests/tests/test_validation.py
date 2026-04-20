"""test_validation.py -- Tests for downstream_tests.validation."""

from downstream_tests.validation import validate_consumer


def test_valid_names():
    assert validate_consumer("nanvix/zlib") is True
    assert validate_consumer("my-org/my.repo") is True
    assert validate_consumer("a_b/c-d.e") is True


def test_invalid_empty():
    assert validate_consumer("") is False


def test_invalid_no_slash():
    assert validate_consumer("nanvix") is False


def test_invalid_double_slash():
    assert validate_consumer("a/b/c") is False


def test_invalid_path_traversal():
    assert validate_consumer("../etc/passwd") is False
    assert validate_consumer("nanvix/../../etc") is False


def test_invalid_special_chars():
    assert validate_consumer("nan;vix/zlib") is False
    assert validate_consumer("nanvix/zlib$(cmd)") is False


def test_invalid_dotdot_components():
    """Reject '..' as owner or repo even though it matches [a-zA-Z0-9_.-]+.

    Regression: '../passwd' previously passed the regex, allowing path
    traversal outside repos_root.
    """
    assert validate_consumer("../passwd") is False
    assert validate_consumer("nanvix/..") is False
    assert validate_consumer("../..") is False


def test_invalid_dot_components():
    """Reject '.' as owner or repo (current-directory traversal)."""
    assert validate_consumer("./repo") is False
    assert validate_consumer("owner/.") is False
