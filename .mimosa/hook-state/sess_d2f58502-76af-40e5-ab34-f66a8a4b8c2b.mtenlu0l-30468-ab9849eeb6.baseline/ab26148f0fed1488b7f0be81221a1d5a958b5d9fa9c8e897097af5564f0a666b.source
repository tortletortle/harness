"""
修复 Windows GBK 编码问题
将所有 emoji 替换为文本标记
"""
import os
import re

# 替换规则
REPLACEMENTS = {
    '✅': '[OK]',
    '❌': '[FAIL]',
    '⚠️': '[WARN]',
    '🎉': '[SUCCESS]',
    '💡': '[TIP]',
    '📝': '[NOTE]',
    '🔴': '[ERROR]',
    '🟡': '[WARNING]',
    '🟢': '[INFO]',
}

# 需要修复的文件
FILES_TO_FIX = [
    'designer/__init__.py',
    'executor/__init__.py',
    'api/main.py',
    'plugin_system.py',
    'launch_server.py',
]

def fix_file(filepath):
    """修复单个文件"""
    if not os.path.exists(filepath):
        print(f"[SKIP] 文件不存在: {filepath}")
        return False
    
    print(f"[处理] {filepath}")
    
    try:
        # 读取文件（使用 UTF-8）
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        original_content = content
        changes = 0
        
        # 替换所有 emoji
        for emoji, replacement in REPLACEMENTS.items():
            if emoji in content:
                count = content.count(emoji)
                content = content.replace(emoji, replacement)
                changes += count
                print(f"  替换 {emoji} -> {replacement}: {count} 次")
        
        if changes > 0:
            # 写回文件
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"  [完成] 共修改 {changes} 处")
            return True
        else:
            print(f"  [跳过] 无需修改")
            return False
    
    except Exception as e:
        print(f"  [错误] {e}")
        return False

def main():
    print("=" * 70)
    print("Windows GBK 编码问题修复工具")
    print("=" * 70)
    print()
    
    fixed_count = 0
    total_count = 0
    
    for filepath in FILES_TO_FIX:
        total_count += 1
        if fix_file(filepath):
            fixed_count += 1
        print()
    
    print("=" * 70)
    print(f"修复完成: {fixed_count}/{total_count} 个文件已修改")
    print("=" * 70)
    
    if fixed_count > 0:
        print("\n[重要] 请重启后端服务器以加载修复后的代码:")
        print("  1. 终止所有 Python 进程")
        print("  2. 运行: python launch_server.py")
        print("  3. 或运行: python -m uvicorn api.server:app --host 127.0.0.1 --port 8000")

if __name__ == "__main__":
    main()
