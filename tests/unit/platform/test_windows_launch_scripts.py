"""Windows 双击安装与启动入口的可移植性回归测试。"""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[3]


def read_script(relative_path: str) -> str:
    """读取 Windows 脚本，并兼容 PowerShell 为 5.1 准备的 UTF-8 BOM。"""
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8-sig")


def test_batch_entrypoints_locate_the_repository_without_fixed_drive() -> None:
    """换盘符或父目录后，四个 BAT 仍以自身位置作为 PMS 根目录。"""
    first_install = read_script("PMS-首次安装.bat")
    daily_start = read_script("PMS-启动.bat")
    legacy_extract = read_script("PMS-提取旧数据.bat")
    legacy_import = read_script("PMS-导入旧主数据.bat")
    combined = first_install + daily_start + legacy_extract + legacy_import

    assert 'cd /d "%~dp0"' in first_install
    assert 'cd /d "%~dp0"' in daily_start
    assert 'cd /d "%~dp0"' in legacy_extract
    assert 'cd /d "%~dp0"' in legacy_import
    assert "%~dp0scripts\\windows\\initialize-pms.ps1" in first_install
    assert "%~dp0scripts\\windows\\start-pms.ps1" in first_install
    assert "%~dp0scripts\\windows\\start-pms.ps1" in daily_start
    assert "%~dp0scripts\\windows\\extract-legacy-data.ps1" in legacy_extract
    assert "%~dp0scripts\\windows\\import-legacy-master-data.ps1" in legacy_import
    assert 'cd /d "D:\\Github\\pms"' not in combined
    assert all(character.isascii() for character in combined)


def test_first_install_and_daily_start_keep_the_supported_command_path() -> None:
    """双击包装不能绕过锁文件、迁移、显式初始化或正式启动器。"""
    first_install = read_script("scripts/windows/initialize-pms.ps1")
    daily_start = read_script("scripts/windows/start-pms.ps1")

    assert '@("python", "install", "3.14.7")' in first_install
    assert '@("sync", "--locked", "--all-groups")' in first_install
    assert '"manage.py", "migrate", "--noinput"' in first_install
    assert '"manage.py", "initialize_pms", "--no-color"' in first_install
    assert 'Read-Host "请设置或输入初始 admin 密码" -AsSecureString' in first_install
    assert "Remove-Item Env:PMS_INITIAL_ADMIN_PASSWORD" in first_install
    assert '$env:PYTHONIOENCODING = "utf-8"' in first_install
    assert "& uv run python manage.py launch_local" in daily_start
    assert '$env:PMS_BIND_HOST = "127.0.0.1"' in daily_start
    assert '$env:PMS_DEBUG = "false"' in daily_start
    assert 'Join-Path $repositoryRoot "data"' in daily_start
    assert '$env:PYTHONIOENCODING = "utf-8"' in daily_start


def test_legacy_extract_script_uses_ignored_sources_and_explicit_sensitive_flag() -> None:
    """双击提取只读旧目录，并显式声明真实敏感数据范围。"""
    script = read_script("scripts/windows/extract-legacy-data.ps1")

    assert 'Join-Path $repositoryRoot ".internal\\legacy-pms"' in script
    assert 'Join-Path $repositoryRoot ".internal\\migration"' in script
    assert "manage.py extract_legacy_data" in script
    assert "--include-restricted" in script
    assert 'Get-Date -Format "yyyyMMdd-HHmmss"' in script
    assert "cd D:\\Github\\pms" not in script


def test_legacy_master_data_import_requires_backup_and_formal_use_case() -> None:
    """双击导入在正式数据库前先备份，并调用版本化映射和应用用例入口。"""
    script = read_script("scripts/windows/import-legacy-master-data.ps1")

    assert 'Join-Path $repositoryRoot "data"' in script
    assert 'manage.py", "map_legacy_master_data' in script
    assert 'manage.py", "backup_local' in script
    assert 'manage.py", "import_legacy_master_data' in script
    assert "GetActiveTcpListeners" in script
    assert "PMS-启动.bat" in script
    assert "cd D:\\Github\\pms" not in script
