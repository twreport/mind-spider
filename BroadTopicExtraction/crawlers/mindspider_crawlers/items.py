# -*- coding: utf-8 -*-
"""
MindSpider Scrapy Items 定义

定义爬取数据的标准化结构
"""

import scrapy


class HotSearchItem(scrapy.Item):
    """热搜数据项"""

    # 必填字段
    title = scrapy.Field()  # 标题
    url = scrapy.Field()  # 链接
    position = scrapy.Field()  # 排名位置
    platform = scrapy.Field()  # 平台 (weibo, douyin, etc.)

    # 时变字段
    hot_value = scrapy.Field()  # 热度值

    # 可选字段
    category = scrapy.Field()  # 分类标签
    description = scrapy.Field()  # 描述
    image = scrapy.Field()  # 图片 URL
    extra = scrapy.Field()  # 额外数据 (dict)


class LocalHotItem(scrapy.Item):
    """地方热搜数据项"""

    title = scrapy.Field()
    url = scrapy.Field()
    position = scrapy.Field()
    platform = scrapy.Field()
    region = scrapy.Field()  # 地区代码

    hot_value = scrapy.Field()

    category = scrapy.Field()
    description = scrapy.Field()
    extra = scrapy.Field()


class VerticalHotItem(scrapy.Item):
    """行业/垂直榜单数据项"""

    title = scrapy.Field()
    url = scrapy.Field()
    position = scrapy.Field()
    platform = scrapy.Field()
    vertical = scrapy.Field()  # 垂直领域 (tech, finance, game, etc.)

    # 时变字段 (根据平台不同)
    hot_value = scrapy.Field()
    stars = scrapy.Field()  # GitHub stars
    replies = scrapy.Field()  # 回复数
    likes = scrapy.Field()  # 点赞数
    rating = scrapy.Field()  # 评分

    category = scrapy.Field()
    description = scrapy.Field()
    author = scrapy.Field()
    extra = scrapy.Field()


class MediaItem(scrapy.Item):
    """传统媒体文章数据项"""

    title = scrapy.Field()
    url = scrapy.Field()
    platform = scrapy.Field()
    media_type = scrapy.Field()  # central, finance, local

    # 文章内容
    content = scrapy.Field()  # 正文
    summary = scrapy.Field()  # 摘要
    publish_date = scrapy.Field()  # 发布日期
    publish_time = scrapy.Field()  # 发布时间 (ISO 格式)
    author = scrapy.Field()  # 作者

    # 可选字段
    category = scrapy.Field()
    tags = scrapy.Field()  # 标签列表
    image = scrapy.Field()
    extra = scrapy.Field()


class WechatItem(scrapy.Item):
    """微信公众号文章数据项"""

    title = scrapy.Field()
    url = scrapy.Field()
    platform = scrapy.Field()  # 固定为 "wechat"
    account_id = scrapy.Field()  # 公众号 ID
    account_name = scrapy.Field()  # 公众号名称
    account_type = scrapy.Field()  # politics, finance, tech

    # 文章内容
    content = scrapy.Field()
    summary = scrapy.Field()
    publish_date = scrapy.Field()
    publish_time = scrapy.Field()
    author = scrapy.Field()

    # 时变字段
    read_count = scrapy.Field()  # 阅读数
    like_count = scrapy.Field()  # 点赞数

    # 可选字段
    cover_image = scrapy.Field()
    extra = scrapy.Field()
