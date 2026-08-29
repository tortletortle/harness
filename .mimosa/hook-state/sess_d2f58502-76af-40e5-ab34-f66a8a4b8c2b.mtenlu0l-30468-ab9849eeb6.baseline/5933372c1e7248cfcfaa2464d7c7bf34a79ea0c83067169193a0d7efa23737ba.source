#!/usr/bin/env python3
"""
文件监控和自动重启服务
监控 Python 文件变化，自动重启后端服务
"""

import sys
import time
import subprocess
import os
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# 监控的目录
WATCH_DIRS = [
    PROJECT_ROOT / "python",
]

# 监控的文件扩展名
WATCH_EXTENSIONS = {'.py'}

# 忽略的目录
IGNORE_DIRS = {'__pycache__', '.git', 'venv', 'env', '.pytest_cache'}


class CodeChangeHandler(FileSystemEventHandler):
    """代码变化处理器"""
    
    def __init__(self):
        self.server_process = None
        self.last_restart_time = 0
        self.restart_delay = 1  # 防抖延迟（秒）
        
    def should_restart(self, event):
        """判断是否应该重启"""
        # 忽略目录事件
        if event.is_directory:
            return False
        
        # 检查文件扩展名
        file_path = Path(event.src_path)
        if file_path.suffix not in WATCH_EXTENSIONS:
            return False
        
        # 检查是否在忽略目录中
        for parent in file_path.parents:
            if parent.name in IGNORE_DIRS:
                return False
        
        # 防抖：避免短时间内多次重启
        current_time = time.time()
        if current_time - self.last_restart_time < self.restart_delay:
            return False
        
        return True
    
    def on_modified(self, event):
        """文件被修改"""
        if self.should_restart(event):
            file_path = Path(event.src_path)
            relative_path = file_path.relative_to(PROJECT_ROOT)
            print(f"\n📝 检测到文件变化: {relative_path}")
            self.restart_server()
    
    def on_created(self, event):
        """文件被创建"""
        if self.should_restart(event):
            file_path = Path(event.src_path)
            relative_path = file_path.relative_to(PROJECT_ROOT)
            print(f"\n📄 检测到新文件: {relative_path}")
            self.restart_server()
    
    def restart_server(self):
        """重启服务器"""
        self.last_restart_time = time.time()
        
        # 终止现有进程
        if self.server_process and self.server_process.poll() is None:
            print("🛑 停止现有服务...")
            self.server_process.terminate()
            try:
                self.server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("⚠️  强制终止服务...")
                self.server_process.kill()
        
        # 启动新进程
        print("🔄 重启服务...")
        time.sleep(0.5)  # 短暂延迟确保端口释放
        self.start_server()
    
    def start_server(self):
        """启动服务器"""
        print("🚀 启动 API 服务...")
        
        # 使用 start_api.py 脚本
        self.server_process = subprocess.Popen(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "start_api.py")],
            cwd=str(PROJECT_ROOT),
        )
        print(f"✅ 服务已启动 (PID: {self.server_process.pid})")


def main():
    """主函数"""
    print("=" * 70)
    print("  EAS 2.0 - 开发模式（自动重启）")
    print("=" * 70)
    print()
    print("📂 监控目录:")
    for watch_dir in WATCH_DIRS:
        if watch_dir.exists():
            print(f"   - {watch_dir.relative_to(PROJECT_ROOT)}")
        else:
            print(f"   - {watch_dir.relative_to(PROJECT_ROOT)} (不存在)")
    print()
    print("📌 监控文件类型:", ", ".join(WATCH_EXTENSIONS))
    print("⏸️  按 Ctrl+C 停止")
    print()
    
    # 创建事件处理器
    handler = CodeChangeHandler()
    
    # 启动初始服务
    handler.start_server()
    
    # 创建观察者
    observer = Observer()
    for watch_dir in WATCH_DIRS:
        if watch_dir.exists():
            observer.schedule(handler, str(watch_dir), recursive=True)
            print(f"👀 正在监控: {watch_dir.relative_to(PROJECT_ROOT)}")
    
    print()
    print("=" * 70)
    print()
    
    # 启动观察者
    observer.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n🛑 停止监控...")
        observer.stop()
        if handler.server_process and handler.server_process.poll() is None:
            print("🛑 停止服务...")
            handler.server_process.terminate()
            handler.server_process.wait()
    
    observer.join()
    print("👋 再见！")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
