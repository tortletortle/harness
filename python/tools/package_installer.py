"""
包管理工具
用于安装依赖包
"""

import subprocess
from typing import Any, Dict, List


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
                command = f"pip install -r {requirements_file}"
            elif packages:
                packages_str = " ".join(packages)
                command = f"pip install {packages_str}"
            else:
                return {
                    "success": False,
                    "message": "必须指定packages或requirements_file",
                    "error": "Missing parameters"
                }
            
            result = subprocess.run(
                command,
                shell=True,
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
            if package_json:
                command = "npm install"
            elif packages:
                packages_str = " ".join(packages) if isinstance(packages, list) else packages
                # 如果是 dev 依赖，添加 --save-dev
                dev_flag = " --save-dev" if dev else ""
                command = f"npm install {packages_str}{dev_flag}"
            else:
                return {
                    "success": False,
                    "message": "必须指定packages或package_json",
                    "error": "Missing parameters"
                }
            
            result = subprocess.run(
                command,
                shell=True,
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
            
            return PackageInstaller.install_npm(
                packages=packages,
                package_json=inputs.get("package_json", False),
                cwd=inputs.get("cwd") or inputs.get("path"),  # 也处理 'path' 键
                dev=inputs.get("dev", False)
            )
        else:
            return {
                "success": False,
                "message": f"未知指令: {instruction}",
                "error": "Invalid instruction"
            }
