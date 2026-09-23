from pathlib import Path
import pytest

from rhino_bridge.server import _safe, _validate_command, _is_secret


def test_rhino_path_traversal_blocked(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(PermissionError):
        _safe(root.resolve(), "../outside")


def test_valid_path(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "src").mkdir()
    assert _safe(root.resolve(), "src") == (root / "src").resolve()


@pytest.mark.parametrize("argv", [
    ["rm", "-rf", "."],
    ["git", "reset", "--hard"],
    ["git", "clean", "-fdx"],
    ["powershell", "-Command", "Get-ChildItem"],
])
def test_bad_commands(argv):
    with pytest.raises(PermissionError):
        _validate_command(argv)


def test_good_command():
    assert _validate_command(["git", "status"]) == ["git", "status"]


def test_secret_detection(tmp_path):
    assert _is_secret(tmp_path / ".env")
    assert _is_secret(tmp_path / "server.key")
    assert not _is_secret(tmp_path / "main.py")
