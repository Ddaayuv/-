import pytest

from bot.backup_schema import CURRENT_BACKUP_VERSION, validate_backup_payload


def test_current_and_legacy_backup_versions_are_supported():
    assert validate_backup_payload({"version": CURRENT_BACKUP_VERSION, "items": []}) == 3
    assert validate_backup_payload({"version": 2, "items": [], "users": []}) == 2
    assert validate_backup_payload({"items": []}) == 1


@pytest.mark.parametrize("version", [0, -1, 4, "3", None, True])
def test_unsupported_backup_version_is_rejected(version):
    with pytest.raises(ValueError, match="نسخة النسخة الاحتياطية"):
        validate_backup_payload({"version": version, "items": []})


def test_invalid_backup_shape_is_rejected():
    with pytest.raises(ValueError, match="كائن JSON"):
        validate_backup_payload([])
    with pytest.raises(ValueError, match="items"):
        validate_backup_payload({"version": 3, "items": {}})
    with pytest.raises(ValueError, match="users"):
        validate_backup_payload({"version": 3, "items": [], "users": {}})
