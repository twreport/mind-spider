// MongoDB 索引定义
// 数据库: mindspider_raw
//
// 使用方法:
// mongosh mindspider_raw < mongo_indexes.js
// 或
// mongo mindspider_raw < mongo_indexes.js

// ============================================
// hot_national - 全国热搜
// ============================================

db.hot_national.createIndex(
    { "item_id": 1 },
    { unique: true, name: "idx_item_id" }
);

db.hot_national.createIndex(
    { "source": 1, "first_seen_at": -1 },
    { name: "idx_source_time" }
);

db.hot_national.createIndex(
    { "platform": 1, "last_seen_at": -1 },
    { name: "idx_platform_time" }
);

db.hot_national.createIndex(
    { "title": "text" },
    { name: "idx_title_text", default_language: "none" }
);

db.hot_national.createIndex(
    { "first_seen_at": -1 },
    { name: "idx_first_seen" }
);

// ============================================
// hot_local - 地方热搜
// ============================================

db.hot_local.createIndex(
    { "item_id": 1 },
    { unique: true, name: "idx_item_id" }
);

db.hot_local.createIndex(
    { "source": 1, "region": 1, "first_seen_at": -1 },
    { name: "idx_source_region_time" }
);

db.hot_local.createIndex(
    { "region": 1, "last_seen_at": -1 },
    { name: "idx_region_time" }
);

db.hot_local.createIndex(
    { "title": "text" },
    { name: "idx_title_text", default_language: "none" }
);

// ============================================
// hot_vertical - 行业/垂直榜单
// ============================================

db.hot_vertical.createIndex(
    { "item_id": 1 },
    { unique: true, name: "idx_item_id" }
);

db.hot_vertical.createIndex(
    { "source": 1, "vertical": 1, "first_seen_at": -1 },
    { name: "idx_source_vertical_time" }
);

db.hot_vertical.createIndex(
    { "platform": 1, "vertical": 1 },
    { name: "idx_platform_vertical" }
);

db.hot_vertical.createIndex(
    { "title": "text" },
    { name: "idx_title_text", default_language: "none" }
);

// ============================================
// media - 传统媒体
// ============================================

db.media.createIndex(
    { "item_id": 1 },
    { unique: true, name: "idx_item_id" }
);

db.media.createIndex(
    { "source": 1, "publish_date": -1 },
    { name: "idx_source_date" }
);

db.media.createIndex(
    { "platform": 1, "media_type": 1, "publish_date": -1 },
    { name: "idx_platform_type_date" }
);

db.media.createIndex(
    { "publish_date": -1 },
    { name: "idx_publish_date" }
);

db.media.createIndex(
    { "title": "text", "content": "text" },
    { name: "idx_fulltext", default_language: "none", weights: { title: 10, content: 1 } }
);

// ============================================
// wechat - 微信公众号
// ============================================

db.wechat.createIndex(
    { "item_id": 1 },
    { unique: true, name: "idx_item_id" }
);

db.wechat.createIndex(
    { "account_id": 1, "publish_date": -1 },
    { name: "idx_account_date" }
);

db.wechat.createIndex(
    { "account_type": 1, "last_seen_at": -1 },
    { name: "idx_type_time" }
);

db.wechat.createIndex(
    { "title": "text", "content": "text" },
    { name: "idx_fulltext", default_language: "none", weights: { title: 10, content: 1 } }
);

// ============================================
// 打印索引信息
// ============================================

print("\n=== 索引创建完成 ===\n");

print("hot_national 索引:");
printjson(db.hot_national.getIndexes());

print("\nhot_local 索引:");
printjson(db.hot_local.getIndexes());

print("\nhot_vertical 索引:");
printjson(db.hot_vertical.getIndexes());

print("\nmedia 索引:");
printjson(db.media.getIndexes());

print("\nwechat 索引:");
printjson(db.wechat.getIndexes());
