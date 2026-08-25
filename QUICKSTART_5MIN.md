# ⚡ 5分钟快速开始

## 第1步：配置API密钥 (1分钟)

编辑 `.env` 文件（项目根目录）：

```bash
ANTHROPIC_API_KEY=sk-ant-xxxxx  # 必需：填入您的Anthropic密钥
```

## 第2步：安装依赖 (2分钟)

```bash
cd python
pip install -r requirements.txt
cd ..
```

## 第3步：运行CLI工具 (2分钟)

```bash
python scripts/cli.py
```

输入您的需求，例如：
```
> 创建一个待办事项应用，支持添加、删除和完成任务
```

AI会：
1. 生成详细的技术设计（架构、组件、难度）
2. 询问是否生成执行计划
3. 生成分步骤的实施方案

## 🎉 完成！

您现在可以：
- ✅ 生成任何项目的技术设计
- ✅ 获得详细的实施计划
- ✅ 评估项目难度和所需工具

---

## 💡 示例输入

试试这些需求：

```
创建一个博客系统，支持Markdown编辑和评论

创建一个在线商城，包含商品管理、购物车和支付

创建一个实时聊天应用，支持群聊和私聊

创建一个图片分享社区，类似Instagram
```

---

## 🔧 遇到问题？

### 问题1: ModuleNotFoundError
```bash
cd python
pip install -r requirements.txt
```

### 问题2: API密钥错误
检查 `.env` 文件中的 `ANTHROPIC_API_KEY`

### 问题3: 其他错误
```bash
python scripts/check_env.py  # 检查环境
```

---

## 📖 更多信息

- **当前功能**: 查看 `STATUS_CURRENT.md`
- **完整总结**: 查看 `PROJECT_SUMMARY.md`
- **开发文档**: 查看 `docs/DEVELOPMENT.md`

---

**就这么简单！ 🚀**
