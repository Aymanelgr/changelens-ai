import io
import stat
import zipfile

import pytest

from app.modules.code_analysis.archive import ArchiveRejected, read_python_archive

LIMITS = {"max_files": 10, "max_uncompressed_bytes": 1_000_000, "max_python_file_bytes": 10_000}


def _zip(entries) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_reads_python_files_and_skips_the_rest():
    contents = read_python_archive(
        _zip({"a.py": "x = 1", "b.bin": "data", "c.md": "doc"}), **LIMITS
    )
    assert list(contents.python_files) == ["a.py"]


def test_wrapper_folder_is_removed():
    contents = read_python_archive(_zip({"repo-main/a/b.py": "", "repo-main/c.py": ""}), **LIMITS)
    assert sorted(contents.python_files) == ["a/b.py", "c.py"]


def test_package_folder_is_kept_because_imports_use_its_name():
    contents = read_python_archive(_zip({"pkg/__init__.py": "", "pkg/mod.py": ""}), **LIMITS)
    assert sorted(contents.python_files) == ["pkg/__init__.py", "pkg/mod.py"]


@pytest.mark.parametrize(
    "name", ["../evil.py", "a/../../evil.py", "/etc/evil.py", "C:/evil.py", "..\\evil.py"]
)
def test_dangerous_paths_are_refused(name):
    with pytest.raises(ArchiveRejected):
        read_python_archive(_zip({name: "x"}), **LIMITS)


def test_symlink_is_refused():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("link.py")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(ArchiveRejected):
        read_python_archive(buffer.getvalue(), **LIMITS)


def test_not_a_zip_is_refused():
    with pytest.raises(ArchiveRejected):
        read_python_archive(b"MZ this is an exe", **LIMITS)


def test_too_many_files_is_refused():
    with pytest.raises(ArchiveRejected):
        read_python_archive(_zip({f"f{i}.py": "" for i in range(11)}), **LIMITS)


def test_zip_bomb_is_refused_before_decompression():
    with pytest.raises(ArchiveRejected):
        read_python_archive(_zip({"bomb.py": "0" * 2_000_000}), **LIMITS)


def test_oversized_python_file_is_skipped_with_a_warning():
    contents = read_python_archive(_zip({"big.py": "#" * 20_000, "ok.py": "x = 1"}), **LIMITS)
    assert list(contents.python_files) == ["ok.py"]
    assert any("big.py" in w for w in contents.warnings)


def test_sensitive_files_are_excluded():
    contents = read_python_archive(
        _zip(
            {
                ".env": "SECRET=1",
                "config/.env.prod": "SECRET=1",
                "deploy/server.pem": "x",
                "keys.py": "KEY = '-----BEGIN RSA PRIVATE KEY-----'",
                "cloud.py": "AWS = 'AKIAABCDEFGHIJKLMNOP'",  # gitleaks:allow (fake key)
                "ok.py": "x = 1",
            }
        ),
        **LIMITS,
    )
    assert list(contents.python_files) == ["ok.py"]
    assert len(contents.excluded_sensitive) == 5
