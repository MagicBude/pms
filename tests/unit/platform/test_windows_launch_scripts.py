"""Windows 双击安装与启动入口的可移植性回归测试。"""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[3]


def read_script(relative_path: str) -> str:
    """读取 Windows 脚本，并兼容 PowerShell 为 5.1 准备的 UTF-8 BOM。"""
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8-sig")


def test_batch_entrypoints_locate_the_repository_without_fixed_drive() -> None:
    """换盘符或父目录后，两个 BAT 仍以自身位置作为 PMS 根目录。"""
    first_install = read_script("PMS-首次安装.bat")
    daily_start = read_script("PMS-启动.bat")
    combined = first_install + daily_start

    assert 'cd /d "%~dp0"' in first_install
    assert 'cd /d "%~dp0"' in daily_start
    assert "%~dp0scripts\\windows\\initialize-pms.ps1" in first_install
    assert "%~dp0scripts\\windows\\start-pms.ps1" in first_install
    assert "%~dp0scripts\\windows\\start-pms.ps1" in daily_start
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
