"""
包管理工具
用于安装依赖包
"""

import re
import shutil
import subprocess
from typing import Any, Dict, List

from tools.workspace import resolve_workspace_path

# 只允许合法的包名/版本约束字符，防止命令注入
# 允许: 字母数字 . _ - + [ ] , 空格(用于extras) 以及版本操作符 = < > ! ~ *
_SAFE_PACKAGE_RE = re.compile(r"^[A-Za-z0-9._+\-\[\],<>=!~* ]+$")


def _validate_packages(packages: List[str]) -> None:
    """校验包名合法性，拒绝包含shell元字符的输入"""
    for pkg in packages:
        if not isinstance(pkg, str) or not pkg.strip():
            raise ValueError(f"非法包名: {pkg!r}")
        if not _SAFE_PACKAGE_RE.match(pkg):
            raise ValueError(f"包名包含非法字符，拒绝执行: {pkg!r}")


class PackageInstaller:
    """包管理工具"""
    
    @staticmethod
    def get_metadata() -> Dict[str, Any]:
        """获取工具元数据"""
        return {
            "tool_id": "package_installer",
            "name": "包管理工具",
            "description": "安装和管理依赖包",
            "category": "generation",
            "version": "1.0.0",
            "capabilities": {
                "instructions": [
                    {
                        "name": "install_pip",
                        "description": "安装Python包",
                        "parameters": {
                            "packages": "包名列表",
                            "requirements_file": "requirements.txt路径（可选）"
                        }
                    },
                    {
                        "name": "install_npm",
                        "description": "安装Node.js包",
                        "parameters": {
                            "packages": "包名列表",
                            "package_json": "package.json路径（可选）",
                            "cwd": "工作目录"
                        }
                    }
                ]
            }
        }
    
    @staticmethod
    def install_pip(packages: List[str] = None, requirements_file: str = None) -> Dict[str, Any]:
        """安装Python包"""
        try:
            if requirements_file:
                if not isinstance(requirements_file, str) or not requirements_file.strip():
                    return {
                        "success": False,
                        "message": "requirements_file 非法",
                        "error": "Invalid parameter"
                    }
                command = ["pip", "install", "-r", requirements_file]
            elif packages:
                try:
                    _validate_packages(packages)
                except ValueError as e:
                    return {
                        "success": False,
                        "message": str(e),
                        "error": "Invalid package name",
                        "blocked": True,
                    }
                command = ["pip", "install", *packages]
            else:
                return {
                    "success": False,
                    "message": "必须指定packages或requirements_file",
                    "error": "Missing parameters"
                }

            result = subprocess.run(
                command,
                shell=False,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            return {
                "success": result.returncode == 0,
                "message": "Python包安装完成" if result.returncode == 0 else "Python包安装失败",
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"安装失败: {str(e)}",
                "error": str(e)
            }
    
    @staticmethod
    def install_npm(packages: List[str] = None, package_json: bool = False, cwd: str = None, dev: bool = False) -> Dict[str, Any]:
        """安装Node.js包"""
        try:
            npm_exe = shutil.which("npm") or "npm"
            if package_json:
                command = [npm_exe, "install"]
            elif packages:
                pkg_list = packages if isinstance(packages, list) else [packages]
                try:
                    _validate_packages(pkg_list)
                except ValueError as e:
                    return {
                        "success": False,
                        "message": str(e),
                        "error": "Invalid package name",
                        "blocked": True,
                    }
                command = [npm_exe, "install", *pkg_list]
                if dev:
                    command.append("--save-dev")
            else:
                return {
                    "success": False,
                    "message": "必须指定packages或package_json",
                    "error": "Missing parameters"
                }

            result = subprocess.run(
                command,
                shell=False,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            return {
                "success": result.returncode == 0,
                "message": "Node.js包安装完成" if result.returncode == 0 else "Node.js包安装失败",
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"安装失败: {str(e)}",
                "error": str(e)
            }
    
    @staticmethod
    def execute(instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具指令"""
        inputs = inputs or {}
        if instruction == "install_pip":
            return PackageInstaller.install_pip(
                packages=inputs.get("packages"),
                requirements_file=inputs.get("requirements_file")
            )
        elif instruction == "install_npm":
            # 处理两种格式：
            # 1. 标准格式: {'packages': ['pkg1', 'pkg2'], 'cwd': '...', 'dev': True}
            # 2. Designer生成的格式: {'package': 'electron', 'version': 'latest', 'dev': True}
            packages = inputs.get("packages")
            if not packages and "package" in inputs:
                # 处理 Designer 生成的单个包格式
                pkg = inputs.get("package")
                version = inputs.get("version")
                if version and version != "latest":
                    packages = [f"{pkg}@{version}"]
                else:
                    packages = [pkg]
            
            # cwd 相对时基于工作区根解析；'path' 键兼容 Designer 生成的格式
            raw_cwd = inputs.get("cwd") or inputs.get("path")
            return PackageInstaller.install_npm(
                packages=packages,
                package_json=inputs.get("package_json", False),
                cwd=str(resolve_workspace_path(raw_cwd)) if raw_cwd else None,
                dev=inputs.get("dev", False)
            )
        else:
            return {
                "success": False,
                "message": f"未知指令: {instruction}",
                "error": "Invalid instruction"
            }
