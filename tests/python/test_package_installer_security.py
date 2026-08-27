# -*- coding: utf-8 -*-
"""
阶段1.1回归测试：package_installer 命令注入修复
- 恶意包名被拒绝
- 合法包名通过校验
- install_pip/install_npm 顶层接口对恶意输入返回 success=False
- subprocess.run 使用列表参数(shell=False)而非字符串+shell=True
"""
import inspect
import subprocess

import pytest

from tools.package_installer import PackageInstaller, _validate_packages


@pytest.mark.parametrize(
    "package",
    ["requests", "numpy==1.26.0", "flask[async]"],
)
def test_validate_packages_accepts_legit_names(package):
    _validate_packages([package])  # should not raise


@pytest.mark.parametrize(
    "payload",
    [
        "requests; rm -rf /",
        "requests && echo pwned",
        "requests`whoami`",
        "requests$(whoami)",
        "requests|cat /etc/passwd",
        "requests & del /f /q C:\\*",
        "requests > /tmp/x",
        "requests\nrm -rf /",
    ],
)
def test_validate_packages_rejects_injection_payloads(payload):
    with pytest.raises(ValueError):
        _validate_packages([payload])


def test_install_pip_rejects_malicious_package():
    result = PackageInstaller.install_pip(packages=["requests; rm -rf /"])
    assert result.get("success") is False
    assert result.get("error") == "Invalid package name"


def test_install_npm_rejects_malicious_package():
    result = PackageInstaller.install_npm(packages=["electron`touch /tmp/pwned`"])
    assert result.get("success") is False
    assert result.get("error") == "Invalid package name"


def test_install_pip_source_uses_shell_false():
    src = inspect.getsource(PackageInstaller.install_pip)
    assert "shell=True" not in src
    assert "shell=False" in src


def test_install_npm_source_uses_shell_false():
    src = inspect.getsource(PackageInstaller.install_npm)
    assert "shell=True" not in src
    assert "shell=False" in src


def test_subprocess_list_form_still_works():
    proc = subprocess.run(["pip", "--version"], shell=False, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0
