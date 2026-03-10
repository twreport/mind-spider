# 远程服务器信息

## SSH 连接

- IP: `10.168.1.80`
- 用户名: `myroot`
- 密码: `tw7311`
- su 密码: `tw7311`
- 连接方式: `sshpass -p 'tw7311' ssh -o StrictHostKeyChecking=no myroot@10.168.1.80`
- sudo 执行: `echo 'tw7311' | sudo -S ...`，stderr 重定向 `2>/dev/null`
- su 执行: `echo 'tw7311' | su -c '...' 2>/dev/null`

## 部署路径

- 代码目录: `/deploy/parallel-universe/mind-spider`
- Python 路径: `/root/anaconda3/envs/mind-spider/bin/python`
- 代码更新: git push → 远程 `su -c 'cd /deploy/parallel-universe/mind-spider && git pull'`

## systemd 服务

- `mindspider-broad-crawl`: 表层采集调度器 (start_scheduler.py)
- `mindspider-deep-crawl`: 深层爬取调度器 (start_deep_crawl.py --port 8777)
- 重启: `su -c 'systemctl restart mindspider-deep-crawl'`

## 数据库

### MongoDB

- 地址: `10.168.1.80:27018`
- 无用户名密码
- Docker 容器: `fish-mongodb`

### MySQL

- 地址: `10.168.1.80:3306`
- 用户名: `root`
- 密码: `Tangwei7311Yeti.`

## Redis

- Docker 容器: `bettafish-redis`
- 端口: `6379`
