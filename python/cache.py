"""
缓存管理模块
用于缓存设计书和执行计划，减少 API 调用
"""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Optional, Dict, Any

from logging_config import get_logger
from paths import DATA_DIR, resolve_db_path

logger = get_logger(__name__)


class DesignCache:
    """设计书缓存"""

    # 未覆盖数据库路径时，默认仍落在仓库根 data/；子类覆盖此属性换文件名。
    DEFAULT_CACHE_FILE = DATA_DIR / "design_cache.json"

    def __init__(self, cache_file: Optional[str] = None, max_size: int = 100, ttl: int = 7 * 24 * 3600):
        """
        初始化缓存
        
        Args:
            cache_file: 缓存文件路径；省略时与当前数据库位于同一目录。
            max_size: 最大缓存数量
            ttl: 缓存有效期（秒），默认 7 天
        """
        if cache_file:
            self.cache_file = Path(cache_file)
        else:
            # 在实例创建时解析：验收脚本常在 import cache 之后才设置 EAS_DB_PATH。
            # 隔离 DB 不得继续读写仓库根的共享设计/计划缓存。
            db_path = resolve_db_path()
            if db_path == ":memory:" or db_path.startswith("file:"):
                # SQLite 内存库/URI 没有可靠的文件父目录；至少不要污染仓库缓存。
                cache_dir = Path(tempfile.gettempdir()) / f"eas-cache-{os.getpid()}"
            else:
                cache_dir = Path(db_path).resolve().parent
            self.cache_file = cache_dir / self.DEFAULT_CACHE_FILE.name
        self.max_size = max_size
        self.ttl = ttl
        self.cache: Dict[str, Dict[str, Any]] = self._load_cache()
        # 命中率观测（§上下文缓存优化）：进程内计数，随重启清零。
        self._lookups = 0
        self._hits = 0
    
    def _make_key(self, user_request: str) -> str:
        """生成缓存键"""
        # 标准化输入：转小写、去除首尾空白
        normalized = user_request.lower().strip()
        return hashlib.md5(normalized.encode()).hexdigest()
    
    def _load_cache(self) -> Dict[str, Dict[str, Any]]:
        """加载缓存文件"""
        if not self.cache_file.exists():
            return {}
        
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error("加载缓存失败: {}", e)
            return {}
    
    def _save_cache(self):
        """保存缓存到文件"""
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("保存缓存失败: {}", e)
    
    def _clean_expired(self):
        """清理过期缓存"""
        now = time.time()
        expired_keys = []
        
        for key, entry in self.cache.items():
            if now - entry.get("timestamp", 0) > self.ttl:
                expired_keys.append(key)
        
        for key in expired_keys:
            del self.cache[key]
            logger.debug("清理过期缓存: {}", key)
        
        if expired_keys:
            self._save_cache()
    
    def _evict_if_needed(self):
        """如果超过最大容量，驱逐最少使用的缓存"""
        if len(self.cache) >= self.max_size:
            # 找出使用次数最少的缓存
            least_used_key = min(
                self.cache.items(),
                key=lambda x: x[1].get("hits", 0)
            )[0]
            
            del self.cache[least_used_key]
            logger.debug("缓存已满，驱逐: {}", least_used_key)
            self._save_cache()
    
    def get(self, user_request: str, fingerprint: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        获取缓存的设计书
        
        Args:
            user_request: 用户需求
            fingerprint: 可选指纹（工具目录/模型）。提供时不一致视为未命中，
                并删除陈旧条目——防止"工具清单变了还返回旧设计书"的错误命中。
            
        Returns:
            缓存的设计书数据，如果不存在/已过期/指纹不一致则返回 None
        """
        self._lookups += 1
        # 清理过期缓存
        self._clean_expired()
        
        key = self._make_key(user_request)
        entry = self.cache.get(key)
        
        if entry is None:
            return None
        
        # 检查是否过期
        if time.time() - entry.get("timestamp", 0) > self.ttl:
            del self.cache[key]
            self._save_cache()
            return None
        
        # 指纹校验：目录/模型变了 → 陈旧条目直接淘汰
        if fingerprint is not None and entry.get("fingerprint") != fingerprint:
            logger.info("缓存指纹不一致，淘汰陈旧设计书: {}", key)
            del self.cache[key]
            self._save_cache()
            return None
        
        # 增加命中次数
        self._hits += 1
        entry["hits"] = entry.get("hits", 0) + 1
        entry["last_access"] = time.time()
        self._save_cache()
        
        return entry.get("design")
    
    def set(self, user_request: str, design: Dict[str, Any], fingerprint: Optional[str] = None):
        """
        设置缓存
        
        Args:
            user_request: 用户需求
            design: 设计书数据（字典格式）
            fingerprint: 可选指纹（工具目录/模型），参与命中校验
        """
        # 检查是否需要驱逐
        self._evict_if_needed()
        
        key = self._make_key(user_request)
        self.cache[key] = {
            "design": design,
            "timestamp": time.time(),
            "last_access": time.time(),
            "hits": 0,
            "fingerprint": fingerprint,
            "user_request": user_request  # 保存原始请求以便调试
        }
        
        self._save_cache()
    
    def remove(self, key: str) -> bool:
        """
        移除指定键的缓存条目。

        Returns:
            True 表示命中并移除；False 表示键不存在。
        """
        k = self._make_key(key)
        if k in self.cache:
            del self.cache[k]
            self._save_cache()
            return True
        return False

    def clear(self):
        """清空所有缓存"""
        self.cache = {}
        self._save_cache()
        logger.info("已清空所有缓存")
    
    def stats(self) -> Dict[str, Any]:
        """获取缓存统计信息（含命中率）"""
        now = time.time()
        total_hits = sum(entry.get("hits", 0) for entry in self.cache.values())
        hit_rate = (self._hits / self._lookups) if self._lookups else 0.0
        
        return {
            "total_entries": len(self.cache),
            "total_hits": total_hits,
            "lookups": self._lookups,
            "process_hits": self._hits,
            "hit_rate": round(hit_rate, 4),
            "max_size": self.max_size,
            "ttl_days": self.ttl / (24 * 3600),
            "oldest_entry": min(
                (entry.get("timestamp", now) for entry in self.cache.values()),
                default=now
            ),
        }


class PlanCache(DesignCache):
    """执行计划缓存（继承设计书缓存的逻辑）"""

    DEFAULT_CACHE_FILE = DATA_DIR / "plan_cache.json"

    def __init__(self, cache_file: Optional[str] = None, max_size: int = 100, ttl: int = 7 * 24 * 3600):
        super().__init__(cache_file, max_size, ttl)
