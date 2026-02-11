# -*- coding: utf-8 -*-
"""
YAML 配置加载器

从 config/sources/*.yaml 加载信源配置，支持按分类查询
"""

import yaml
from pathlib import Path
from typing import Dict, List, Optional
from loguru import logger


class ConfigLoader:
    """YAML 配置加载器"""

    def __init__(self, config_dir: Optional[str] = None):
        """
        初始化配置加载器

        Args:
            config_dir: 配置文件目录路径，默认为 BroadTopicExtraction/config/sources
        """
        if config_dir is None:
            # 默认路径：相对于当前文件的 ../config/sources
            config_dir = Path(__file__).parent.parent / "config" / "sources"
        self.config_dir = Path(config_dir)
        self._sources: Dict[str, dict] = {}
        self._schedule_config: dict = {}
        self._load_all()

    def _load_all(self) -> None:
        """加载所有信源配置"""
        if not self.config_dir.exists():
            logger.warning(f"配置目录不存在: {self.config_dir}")
            return

        for yaml_file in self.config_dir.glob("*.yaml"):
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    sources = yaml.safe_load(f)
                    if sources:
                        self._sources.update(sources)
                        logger.debug(f"已加载配置: {yaml_file.name}")
            except Exception as e:
                logger.error(f"加载配置文件失败 {yaml_file}: {e}")

        # 加载全局调度配置
        schedule_file = self.config_dir.parent / "schedule.yaml"
        if schedule_file.exists():
            try:
                with open(schedule_file, "r", encoding="utf-8") as f:
                    self._schedule_config = yaml.safe_load(f) or {}
            except Exception as e:
                logger.error(f"加载调度配置失败: {e}")

    def reload(self) -> None:
        """重新加载所有配置"""
        self._sources.clear()
        self._schedule_config.clear()
        self._load_all()

    def get_source(self, source_name: str) -> Optional[dict]:
        """
        获取指定信源的配置

        支持两种查找方式：
        1. 直接匹配 YAML key（如 cctv_scrapy）
        2. 匹配 spider_name 字段（如 cctv）

        Args:
            source_name: 信源名称或 spider_name

        Returns:
            信源配置字典，不存在则返回 None
        """
        # 直接匹配 YAML key
        if source_name in self._sources:
            return self._sources[source_name]

        # 回退：按 spider_name 查找
        for config in self._sources.values():
            if config.get("spider_name") == source_name:
                return config

        return None

    def get_all_sources(self) -> Dict[str, dict]:
        """获取所有信源配置"""
        return self._sources.copy()

    def get_sources_by_category(self, category: str) -> Dict[str, dict]:
        """
        按分类获取信源

        Args:
            category: 分类代码 (hot_national, hot_local, hot_vertical, media, wechat)

        Returns:
            该分类下的所有信源配置
        """
        return {
            name: config
            for name, config in self._sources.items()
            if config.get("category") == category
        }

    def get_sources_by_type(self, source_type: str) -> Dict[str, dict]:
        """
        按采集方式获取信源

        Args:
            source_type: 采集方式 (scrapy, aggregator)

        Returns:
            该采集方式的所有信源配置
        """
        return {
            name: config
            for name, config in self._sources.items()
            if config.get("source_type") == source_type
        }

    def get_enabled_sources(self) -> Dict[str, dict]:
        """获取所有启用的信源"""
        return {
            name: config
            for name, config in self._sources.items()
            if config.get("enabled", True)
        }

    def get_schedule_config(self) -> dict:
        """获取全局调度配置"""
        return self._schedule_config.copy()

    def list_categories(self) -> List[str]:
        """列出所有分类"""
        categories = set()
        for config in self._sources.values():
            if "category" in config:
                categories.add(config["category"])
        return sorted(categories)

    def list_sources(self) -> List[str]:
        """列出所有信源名称"""
        return sorted(self._sources.keys())
