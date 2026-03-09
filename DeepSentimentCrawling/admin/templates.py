# -*- coding: utf-8 -*-
"""
深层采集监控面板 — 前端 HTML/JS/CSS 模板

单页面 HTML，通过 JS fetch 调 API，Chart.js 画趋势图。
风格与浅层面板保持一致（Ant Design 配色、60s 自动刷新）。
"""

# 平台名称映射
PLATFORM_NAMES = {
    "xhs": "小红书",
    "dy": "抖音",
    "bili": "B站",
    "wb": "微博",
    "ks": "快手",
    "tieba": "贴吧",
    "zhihu": "知乎",
}


def get_dashboard_html(token: str = "") -> str:
    """生成深层采集监控面板 HTML"""
    token_param = f"token={token}" if token else ""
    amp_token = f"&token={token}" if token else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MindSpider 深层采集监控面板</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3/dist/chartjs-plugin-annotation.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #f0f2f5; color: #333; padding: 20px;
        }}
        a {{ color: #1890ff; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}

        /* 顶部导航 */
        .header {{
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 20px; flex-wrap: wrap; gap: 8px;
        }}
        .header h1 {{ font-size: 22px; color: #1a1a2e; }}
        .header-right {{ display: flex; align-items: center; gap: 12px; font-size: 13px; color: #888; }}
        .header-right label {{ cursor: pointer; }}
        .nav-links {{ font-size: 13px; display: flex; gap: 16px; }}

        /* 摘要卡片 */
        .summary {{
            display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px;
            margin-bottom: 24px;
        }}
        .card {{
            background: #fff; border-radius: 8px; padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .card .label {{ font-size: 13px; color: #888; margin-bottom: 8px; }}
        .card .value {{ font-size: 28px; font-weight: 700; }}
        .card .sub {{ font-size: 12px; color: #aaa; margin-top: 4px; }}
        .card.blue .value {{ color: #1890ff; }}
        .card.orange .value {{ color: #faad14; }}
        .card.green .value {{ color: #52c41a; }}
        .card.red .value {{ color: #ff4d4f; }}

        /* 通用区块 */
        .section {{
            background: #fff; border-radius: 8px; padding: 20px;
            margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .section h2 {{ font-size: 16px; margin-bottom: 16px; color: #1a1a2e; }}

        /* 平台健康矩阵 */
        .platform-grid {{
            display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 12px;
        }}
        .plat-card {{
            border: 1px solid #f0f0f0; border-radius: 8px; padding: 14px;
            position: relative;
        }}
        .plat-card .plat-header {{
            display: flex; align-items: center; gap: 8px; margin-bottom: 10px;
            font-weight: 600; font-size: 14px;
        }}
        .plat-card .plat-detail {{ font-size: 12px; color: #666; line-height: 1.8; }}
        .plat-card .plat-detail span {{ color: #333; }}
        .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; }}
        .dot-green {{ background: #52c41a; }}
        .dot-yellow {{ background: #faad14; }}
        .dot-red {{ background: #ff4d4f; }}
        .dot-gray {{ background: #d9d9d9; }}

        /* 任务列表表格 */
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        th {{
            background: #fafafa; padding: 10px 12px; text-align: left;
            border-bottom: 2px solid #f0f0f0; font-weight: 600; color: #666;
        }}
        td {{ padding: 10px 12px; border-bottom: 1px solid #f5f5f5; }}
        tr:hover {{ background: #fafafa; }}
        .status-badge {{
            display: inline-block; padding: 2px 8px; border-radius: 4px;
            font-size: 12px; font-weight: 600;
        }}
        .status-pending {{ background: #e6f7ff; color: #1890ff; }}
        .status-running {{ background: #fff7e6; color: #fa8c16; }}
        .status-completed {{ background: #f6ffed; color: #52c41a; }}
        .status-failed {{ background: #fff2f0; color: #ff4d4f; }}
        .status-cancelled {{ background: #f5f5f5; color: #999; }}

        /* 筛选器 */
        .filter-bar {{
            margin-bottom: 12px; display: flex; gap: 10px; align-items: center;
            flex-wrap: wrap;
        }}
        .filter-bar select, .filter-bar button {{
            padding: 6px 10px; border: 1px solid #d9d9d9; border-radius: 4px;
            font-size: 13px; background: #fff; cursor: pointer;
        }}
        .filter-bar button:hover {{ border-color: #1890ff; color: #1890ff; }}
        .pagination {{ margin-top: 12px; display: flex; gap: 8px; align-items: center; font-size: 13px; }}
        .pagination button {{ padding: 4px 12px; }}

        /* 趋势图 */
        .chart-container {{ position: relative; height: 300px; }}

        /* 错误日志 */
        .error-log {{
            max-height: 400px; overflow-y: auto;
            font-family: "SFMono-Regular", Consolas, monospace;
            font-size: 12px; line-height: 1.6;
        }}
        .error-log .log-entry {{ padding: 6px 0; border-bottom: 1px solid #f5f5f5; }}
        .error-log .log-time {{ color: #888; margin-right: 8px; }}
        .error-log .log-platform {{ color: #1890ff; margin-right: 8px; font-weight: 600; }}
        .error-log .log-msg {{ color: #ff4d4f; }}

        /* 展开行 */
        .expand-row {{ display: none; }}
        .expand-row td {{ background: #fafafa; padding: 12px 20px; }}
        .expand-content {{ font-size: 12px; color: #666; line-height: 1.8; }}

        /* 弹窗 */
        .modal-overlay {{
            display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.45); z-index: 9998;
            align-items: center; justify-content: center;
        }}
        .modal-overlay.active {{ display: flex; }}
        .modal-box {{
            background: #fff; border-radius: 12px; padding: 24px; width: 90%; max-width: 800px;
            max-height: 90vh; overflow-y: auto; box-shadow: 0 8px 30px rgba(0,0,0,0.2);
            position: relative;
        }}
        .modal-box h3 {{ font-size: 16px; margin-bottom: 4px; color: #1a1a2e; padding-right: 36px; }}
        .modal-box .modal-sub {{ font-size: 12px; color: #888; margin-bottom: 16px; }}
        .modal-close {{
            position: absolute; top: 16px; right: 20px; font-size: 22px; color: #999;
            cursor: pointer; line-height: 1; border: none; background: none;
        }}
        .modal-close:hover {{ color: #333; }}
        .modal-chart {{ position: relative; height: 350px; }}

        @media (max-width: 768px) {{
            .summary {{ grid-template-columns: repeat(2, 1fr); }}
            .platform-grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>
    <!-- 顶部导航 -->
    <div class="header">
        <div>
            <h1>MindSpider 深层采集监控</h1>
            <div class="nav-links" style="margin-top: 6px;">
                <a href="/?{token_param}">登录控制台</a>
                <a href="http://{{window.location.hostname}}:8778/?{token_param}" id="shallow-link">浅层面板</a>
            </div>
        </div>
        <div class="header-right">
            <span id="last-updated"></span>
            <label><input type="checkbox" id="auto-refresh" checked> 60s 自动刷新</label>
        </div>
    </div>

    <!-- 总览卡片 -->
    <div class="summary" id="summary-cards">
        <div class="card blue"><div class="label">待执行</div><div class="value" id="v-pending">-</div><div class="sub" id="v-queue">队列: -</div></div>
        <div class="card orange"><div class="label">执行中</div><div class="value" id="v-running">-</div></div>
        <div class="card green"><div class="label">已完成</div><div class="value" id="v-completed">-</div><div class="sub" id="v-completed-total">总计: -</div></div>
        <div class="card red"><div class="label">失败</div><div class="value" id="v-failed">-</div></div>
    </div>

    <!-- 平台健康矩阵 -->
    <div class="section">
        <h2>平台健康状态</h2>
        <div class="platform-grid" id="platform-grid">
            <div style="color:#aaa;">加载中...</div>
        </div>
    </div>

    <!-- 热门候选话题 -->
    <div class="section">
        <h2>24h 热门候选 TOP 10</h2>
        <table>
            <thead>
                <tr>
                    <th>排名</th>
                    <th>话题</th>
                    <th>状态</th>
                    <th>最高热度</th>
                    <th>当前热度</th>
                    <th>跨平台数</th>
                    <th>深爬触发</th>
                    <th>首次出现</th>
                </tr>
            </thead>
            <tbody id="candidates-table-body">
                <tr><td colspan="8" style="text-align:center; color:#aaa;">加载中...</td></tr>
            </tbody>
        </table>
    </div>

    <!-- 任务列表 -->
    <div class="section">
        <h2>任务列表</h2>
        <div class="filter-bar">
            <select id="filter-platform">
                <option value="">全部平台</option>
                <option value="xhs">小红书</option>
                <option value="dy">抖音</option>
                <option value="bili">B站</option>
                <option value="wb">微博</option>
                <option value="ks">快手</option>
                <option value="tieba">贴吧</option>
                <option value="zhihu">知乎</option>
            </select>
            <select id="filter-status">
                <option value="">全部状态</option>
                <option value="pending">待执行</option>
                <option value="running">执行中</option>
                <option value="completed">已完成</option>
                <option value="failed">失败</option>
            </select>
            <button onclick="loadTasks()">筛选</button>
        </div>
        <table>
            <thead>
                <tr>
                    <th>状态</th>
                    <th>平台</th>
                    <th>话题</th>
                    <th>关键词</th>
                    <th>爬取量</th>
                    <th>耗时</th>
                    <th>重试</th>
                    <th>创建时间</th>
                </tr>
            </thead>
            <tbody id="task-table-body">
                <tr><td colspan="8" style="text-align:center; color:#aaa;">加载中...</td></tr>
            </tbody>
        </table>
        <div class="pagination">
            <button onclick="prevPage()">上一页</button>
            <span id="page-info">第 1 页</span>
            <button onclick="nextPage()">下一页</button>
        </div>
    </div>

    <!-- 数据产量趋势图 -->
    <div class="section">
        <h2>数据产量趋势 (48h)</h2>
        <div class="chart-container">
            <canvas id="volume-chart"></canvas>
        </div>
    </div>

    <!-- 错误日志 -->
    <div class="section">
        <h2>错误日志</h2>
        <div class="filter-bar">
            <select id="error-platform-filter">
                <option value="">全部平台</option>
                <option value="xhs">小红书</option>
                <option value="dy">抖音</option>
                <option value="bili">B站</option>
                <option value="wb">微博</option>
                <option value="ks">快手</option>
                <option value="tieba">贴吧</option>
                <option value="zhihu">知乎</option>
            </select>
            <button onclick="loadErrors()">过滤</button>
        </div>
        <div class="error-log" id="error-log">
            <div class="log-entry" style="color:#aaa;">加载中...</div>
        </div>
    </div>

    <script>
        let TOKEN = "{token_param}";
        const AMP_TOKEN = "{amp_token}";
        const PLATFORM_NAMES = {{
            xhs: '小红书', dy: '抖音', bili: 'B站', wb: '微博',
            ks: '快手', tieba: '贴吧', zhihu: '知乎'
        }};
        let volumeChart = null;
        let refreshTimer = null;
        let currentPage = 0;
        const PAGE_SIZE = 30;

        // 修正浅层面板链接
        document.getElementById('shallow-link').href =
            'http://' + window.location.hostname + ':8778/?' + TOKEN;

        // --- Token 鉴权处理 ---
        function promptToken() {{
            const overlay = document.createElement('div');
            overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center;';
            overlay.innerHTML = `
                <div style="background:#fff;padding:32px;border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,0.15);text-align:center;max-width:360px;">
                    <h3 style="margin-bottom:16px;color:#1a1a2e;">请输入访问令牌</h3>
                    <input id="token-input" type="password" placeholder="Token"
                        style="width:100%;padding:10px;border:1px solid #d9d9d9;border-radius:4px;font-size:14px;margin-bottom:16px;">
                    <br>
                    <button onclick="submitToken()"
                        style="padding:8px 24px;background:#1890ff;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:14px;">
                        确认
                    </button>
                </div>`;
            document.body.appendChild(overlay);
            document.getElementById('token-input').focus();
            document.getElementById('token-input').addEventListener('keydown', (e) => {{
                if (e.key === 'Enter') submitToken();
            }});
        }}

        function submitToken() {{
            const val = document.getElementById('token-input').value.trim();
            if (val) {{
                // 将 token 写入 URL 并刷新
                const url = new URL(window.location);
                url.searchParams.set('token', val);
                window.location.href = url.toString();
            }}
        }}

        // --- 数据加载 ---
        async function fetchJSON(url) {{
            const sep = url.includes('?') ? '&' : '?';
            const fullUrl = TOKEN ? url + sep + TOKEN : url;
            const resp = await fetch(fullUrl);
            if (resp.status === 403) {{
                promptToken();
                throw new Error('需要访问令牌');
            }}
            if (!resp.ok) throw new Error(`HTTP ${{resp.status}}`);
            return resp.json();
        }}

        async function loadOverview() {{
            try {{
                const data = await fetchJSON('/dashboard/api/overview');
                document.getElementById('v-pending').textContent = data.pending;
                document.getElementById('v-queue').textContent = 'Redis 队列: ' + data.redis_queue_size;
                document.getElementById('v-running').textContent = data.running;
                document.getElementById('v-completed').textContent = data.completed_today;
                document.getElementById('v-completed-total').textContent = '总计: ' + data.completed_total;
                document.getElementById('v-failed').textContent = data.failed;
            }} catch (e) {{
                console.error('加载总览失败:', e);
            }}
        }}

        async function loadPlatforms() {{
            try {{
                const data = await fetchJSON('/dashboard/api/platforms');
                renderPlatforms(data);
            }} catch (e) {{
                console.error('加载平台状态失败:', e);
            }}
        }}

        async function loadCandidates() {{
            try {{
                const data = await fetchJSON('/dashboard/api/top-candidates?limit=10');
                renderCandidates(data);
            }} catch (e) {{
                console.error('加载候选话题失败:', e);
            }}
        }}

        async function loadTasks() {{
            try {{
                const platform = document.getElementById('filter-platform').value;
                const status = document.getElementById('filter-status').value;
                let url = `/dashboard/api/tasks?limit=${{PAGE_SIZE}}&offset=${{currentPage * PAGE_SIZE}}`;
                if (platform) url += '&platform=' + platform;
                if (status) url += '&status=' + status;
                const data = await fetchJSON(url);
                renderTasks(data);
            }} catch (e) {{
                console.error('加载任务列表失败:', e);
            }}
        }}

        async function loadVolumes() {{
            try {{
                const data = await fetchJSON('/dashboard/api/volumes?hours=48');
                renderChart(data);
            }} catch (e) {{
                console.error('加载趋势失败:', e);
            }}
        }}

        async function loadErrors() {{
            try {{
                const platform = document.getElementById('error-platform-filter').value;
                let url = '/dashboard/api/errors?limit=50';
                if (platform) url += '&platform=' + encodeURIComponent(platform);
                const data = await fetchJSON(url);
                renderErrors(data);
            }} catch (e) {{
                console.error('加载错误日志失败:', e);
            }}
        }}

        // --- 渲染 ---
        function renderPlatforms(platforms) {{
            const container = document.getElementById('platform-grid');
            if (!platforms.length) {{
                container.innerHTML = '<div style="color:#aaa;">暂无数据</div>';
                return;
            }}
            let html = '';
            for (const p of platforms) {{
                const name = PLATFORM_NAMES[p.platform] || p.platform;

                // 状态灯
                let dotClass = 'dot-gray';
                if (p.cookie_status === 'active') {{
                    dotClass = p.circuit_breaker === 'open' ? 'dot-red' : 'dot-green';
                }} else if (p.cookie_status === 'expired') {{
                    dotClass = 'dot-red';
                }}

                const cookieTime = p.cookie_saved_at
                    ? formatTs(p.cookie_saved_at) : '-';
                const circuit = p.circuit_breaker === 'open'
                    ? '<span style="color:#ff4d4f;font-weight:600;">OPEN</span>'
                    : '<span style="color:#52c41a;">closed</span>';

                let lastInfo = '-';
                if (p.last_task) {{
                    const statusColor = {{
                        completed: '#52c41a', failed: '#ff4d4f',
                        running: '#fa8c16', pending: '#1890ff'
                    }}[p.last_task.status] || '#999';
                    const dur = p.last_task.duration ? (p.last_task.duration + 's') : '-';
                    lastInfo = `<span style="color:${{statusColor}}">${{p.last_task.status}}</span> | ${{p.last_task.item_count || 0}} 条 | ${{dur}}`;
                }}

                const rate = p.stats_24h.success_rate !== null
                    ? p.stats_24h.success_rate + '%' : '-';

                html += `
                <div class="plat-card">
                    <div class="plat-header">
                        <span class="dot ${{dotClass}}"></span>
                        ${{name}}
                    </div>
                    <div class="plat-detail">
                        Cookie: <span>${{p.cookie_status}}</span> (${{cookieTime}})<br>
                        熔断器: ${{circuit}}<br>
                        最近任务: ${{lastInfo}}<br>
                        24h 成功率: <span>${{rate}}</span> (${{p.stats_24h.completed}}/${{p.stats_24h.total}})
                    </div>
                </div>`;
            }}
            container.innerHTML = html;
        }}

        function renderTasks(data) {{
            const tbody = document.getElementById('task-table-body');
            const tasks = data.tasks;
            if (!tasks || !tasks.length) {{
                tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#aaa;">暂无任务</td></tr>';
                document.getElementById('page-info').textContent = '共 0 条';
                return;
            }}

            let html = '';
            for (let i = 0; i < tasks.length; i++) {{
                const t = tasks[i];
                const name = PLATFORM_NAMES[t.platform] || t.platform;
                const statusClass = 'status-' + (t.status || 'pending');
                const keywords = Array.isArray(t.search_keywords)
                    ? t.search_keywords.slice(0, 3).join(', ') : '-';
                const crawled = t.total_crawled || '-';
                const duration = (t.started_at && t.completed_at)
                    ? ((t.completed_at - t.started_at) + 's') : '-';
                const attempts = t.attempts || 0;
                const created = t.created_at ? formatTs(t.created_at) : '-';

                html += `<tr onclick="toggleExpand(${{i}})" style="cursor:pointer;">
                    <td><span class="status-badge ${{statusClass}}">${{t.status}}</span></td>
                    <td>${{name}}</td>
                    <td>${{escapeHtml(t.topic_title || '-')}}</td>
                    <td title="${{escapeHtml(keywords)}}">${{escapeHtml(keywords.length > 30 ? keywords.substring(0, 30) + '...' : keywords)}}</td>
                    <td>${{crawled}}</td>
                    <td>${{duration}}</td>
                    <td>${{attempts}}</td>
                    <td>${{created}}</td>
                </tr>
                <tr class="expand-row" id="expand-${{i}}">
                    <td colspan="8">
                        <div class="expand-content">
                            <strong>Task ID:</strong> ${{t.task_id || '-'}}<br>
                            <strong>Candidate:</strong> ${{t.candidate_id || '-'}}<br>
                            <strong>关键词:</strong> ${{escapeHtml(Array.isArray(t.search_keywords) ? t.search_keywords.join(', ') : '-')}}<br>
                            <strong>错误:</strong> ${{escapeHtml(t.error || t.last_error || '无')}}
                        </div>
                    </td>
                </tr>`;
            }}
            tbody.innerHTML = html;

            const total = data.total || 0;
            const totalPages = Math.ceil(total / PAGE_SIZE);
            document.getElementById('page-info').textContent =
                `第 ${{currentPage + 1}} / ${{totalPages}} 页 (共 ${{total}} 条)`;
        }}

        function toggleExpand(idx) {{
            const row = document.getElementById('expand-' + idx);
            if (row) {{
                row.style.display = row.style.display === 'table-row' ? 'none' : 'table-row';
            }}
        }}

        function renderCandidates(candidates) {{
            const tbody = document.getElementById('candidates-table-body');
            if (!candidates || !candidates.length) {{
                tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#aaa;">24h 内暂无候选话题</td></tr>';
                return;
            }}
            let html = '';
            for (let i = 0; i < candidates.length; i++) {{
                const c = candidates[i];
                const statusMap = {{
                    emerging: ['#e6f7ff', '#1890ff'],
                    rising: ['#fff7e6', '#fa8c16'],
                    confirmed: ['#fff1f0', '#f5222d'],
                    exploded: ['#fff1f0', '#cf1322'],
                    tracking: ['#f6ffed', '#52c41a'],
                    closed: ['#f5f5f5', '#999'],
                    faded: ['#f5f5f5', '#ccc'],
                }};
                const [bg, color] = statusMap[c.status] || ['#f5f5f5', '#999'];

                // 热度条：相对于第一名的比例
                const maxOfAll = candidates[0].max_score || 1;
                const pct = Math.round(c.max_score / maxOfAll * 100);
                const barColor = c.triggered ? '#f5222d' : c.confirmed ? '#fa8c16' : '#1890ff';

                let triggerLabel = '<span style="color:#ccc;">—</span>';
                if (c.triggered) triggerLabel = '<span style="color:#f5222d; font-weight:600;">exploded</span>';
                else if (c.confirmed) triggerLabel = '<span style="color:#fa8c16; font-weight:600;">confirmed</span>';

                html += `<tr>
                    <td style="font-weight:600; color:#888;">${{i + 1}}</td>
                    <td style="max-width:280px;">
                        <div class="clickable" onclick="showCandidateChart('${{c.candidate_id}}')">${{escapeHtml(c.title)}}</div>
                        <div style="margin-top:4px; height:4px; border-radius:2px; background:#f0f0f0;">
                            <div style="height:100%; width:${{pct}}%; background:${{barColor}}; border-radius:2px;"></div>
                        </div>
                    </td>
                    <td><span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600;background:${{bg}};color:${{color}};">${{c.status}}</span></td>
                    <td style="font-weight:700;">${{c.max_score.toLocaleString()}}</td>
                    <td>${{c.current_score.toLocaleString()}}</td>
                    <td>${{c.platform_count || '-'}}</td>
                    <td>${{triggerLabel}}</td>
                    <td>${{formatTs(c.first_seen_at)}}</td>
                </tr>`;
            }}
            tbody.innerHTML = html;
        }}

        function renderChart(data) {{
            const ctx = document.getElementById('volume-chart').getContext('2d');
            const allHours = new Set();
            for (const plat of Object.keys(data)) {{
                for (const pt of data[plat]) allHours.add(pt.hour);
            }}
            const labels = Array.from(allHours).sort();

            const colors = {{
                xhs: '#ff2442', dy: '#000000', bili: '#00a1d6',
                wb: '#ff6600', ks: '#ff5000', tieba: '#4e6ef2', zhihu: '#0066ff'
            }};

            const datasets = [];
            for (const [plat, points] of Object.entries(data)) {{
                const countMap = {{}};
                for (const pt of points) countMap[pt.hour] = pt.count;
                const name = PLATFORM_NAMES[plat] || plat;
                datasets.push({{
                    label: name,
                    data: labels.map(h => countMap[h] || 0),
                    borderColor: colors[plat] || '#999',
                    backgroundColor: (colors[plat] || '#999') + '20',
                    tension: 0.3,
                    fill: false,
                }});
            }}

            if (volumeChart) volumeChart.destroy();
            volumeChart = new Chart(ctx, {{
                type: 'line',
                data: {{ labels: labels.map(h => h.slice(5)), datasets }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {{ intersect: false, mode: 'index' }},
                    scales: {{
                        y: {{ beginAtZero: true, title: {{ display: true, text: '完成任务数' }} }},
                        x: {{ title: {{ display: true, text: '时间' }} }},
                    }},
                }},
            }});
        }}

        function renderErrors(errors) {{
            const container = document.getElementById('error-log');
            if (!errors.length) {{
                container.innerHTML = '<div class="log-entry" style="color:#52c41a;">暂无错误日志</div>';
                return;
            }}
            let html = '';
            for (const e of errors) {{
                const plat = e.platform_hint
                    ? `<span class="log-platform">${{PLATFORM_NAMES[e.platform_hint] || e.platform_hint}}</span>` : '';
                html += `<div class="log-entry">
                    <span class="log-time">${{e.time}}</span>
                    ${{plat}}
                    <span class="log-msg">${{escapeHtml(e.message)}}</span>
                </div>`;
            }}
            container.innerHTML = html;
        }}

        // --- 候选热度曲线弹窗 ---
        let candidateChart = null;

        async function showCandidateChart(candidateId) {{
            try {{
                const data = await fetchJSON('/dashboard/api/candidate/' + encodeURIComponent(candidateId));
                document.getElementById('modal-title').textContent = data.title;
                document.getElementById('modal-sub').textContent = '当前状态: ' + data.status;
                document.getElementById('candidate-modal').classList.add('active');
                renderCandidateChart(data);
            }} catch (e) {{
                console.error('加载候选详情失败:', e);
            }}
        }}

        function closeCandidateModal() {{
            document.getElementById('candidate-modal').classList.remove('active');
            if (candidateChart) {{ candidateChart.destroy(); candidateChart = null; }}
        }}

        // ESC 关闭
        document.addEventListener('keydown', (e) => {{
            if (e.key === 'Escape') closeCandidateModal();
        }});

        function renderCandidateChart(data) {{
            const ctx = document.getElementById('candidate-chart').getContext('2d');
            const snaps = data.snapshots || [];
            const transitions = data.transitions || [];

            if (!snaps.length) return;

            // 数据点
            const labels = snaps.map(s => {{
                const d = new Date(s.ts * 1000);
                const pad = n => String(n).padStart(2, '0');
                return `${{pad(d.getMonth()+1)}}-${{pad(d.getDate())}} ${{pad(d.getHours())}}:${{pad(d.getMinutes())}}`;
            }});
            const scores = snaps.map(s => s.score_pos);

            // 状态跃迁标注：匹配最近的 snapshot 时间点
            const statusColors = {{
                emerging: '#1890ff', rising: '#fa8c16', confirmed: '#f5222d',
                exploded: '#cf1322', tracking: '#52c41a', closed: '#999', faded: '#ccc'
            }};
            const annotations = {{}};
            const keyStatuses = new Set(['rising', 'confirmed', 'exploded', 'tracking']);

            for (const t of transitions) {{
                if (!keyStatuses.has(t.status)) continue;
                // 找最近的 snapshot index
                let bestIdx = 0;
                let bestDist = Infinity;
                for (let i = 0; i < snaps.length; i++) {{
                    const dist = Math.abs(snaps[i].ts - t.ts);
                    if (dist < bestDist) {{ bestDist = dist; bestIdx = i; }}
                }}
                const key = 'tr_' + t.status + '_' + t.ts;
                annotations[key] = {{
                    type: 'point',
                    xValue: bestIdx,
                    yValue: snaps[bestIdx].score_pos,
                    backgroundColor: statusColors[t.status] || '#999',
                    borderColor: '#fff',
                    borderWidth: 2,
                    radius: 7,
                }};
                annotations[key + '_label'] = {{
                    type: 'label',
                    xValue: bestIdx,
                    yValue: snaps[bestIdx].score_pos,
                    content: t.status,
                    color: statusColors[t.status] || '#999',
                    font: {{ size: 11, weight: 'bold' }},
                    position: 'start',
                    yAdjust: -16,
                }};
            }}

            // 阈值线
            annotations['line_confirmed'] = {{
                type: 'line', yMin: 4000, yMax: 4000,
                borderColor: '#fa8c1680', borderWidth: 1, borderDash: [6, 4],
                label: {{ display: true, content: 'confirmed (4000)', position: 'start',
                         color: '#fa8c16', font: {{ size: 10 }}, backgroundColor: 'transparent' }}
            }};
            annotations['line_exploded'] = {{
                type: 'line', yMin: 10000, yMax: 10000,
                borderColor: '#f5222d80', borderWidth: 1, borderDash: [6, 4],
                label: {{ display: true, content: 'exploded (10000)', position: 'start',
                         color: '#f5222d', font: {{ size: 10 }}, backgroundColor: 'transparent' }}
            }};

            if (candidateChart) candidateChart.destroy();
            candidateChart = new Chart(ctx, {{
                type: 'line',
                data: {{
                    labels,
                    datasets: [{{
                        label: 'score_pos',
                        data: scores,
                        borderColor: '#1890ff',
                        backgroundColor: '#1890ff20',
                        tension: 0.3,
                        fill: true,
                        pointRadius: 2,
                        pointHoverRadius: 5,
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {{ intersect: false, mode: 'index' }},
                    scales: {{
                        y: {{ beginAtZero: true, title: {{ display: true, text: 'score_pos' }} }},
                        x: {{
                            title: {{ display: true, text: '时间' }},
                            ticks: {{ maxTicksLimit: 12, maxRotation: 45 }},
                        }},
                    }},
                    plugins: {{
                        annotation: {{ annotations }},
                    }},
                }},
            }});
        }}

        // --- 工具函数 ---
        function formatTs(ts) {{
            if (!ts) return '-';
            try {{
                const d = new Date(ts * 1000);
                if (isNaN(d.getTime())) return String(ts);
                const pad = n => String(n).padStart(2, '0');
                return `${{pad(d.getMonth()+1)}}-${{pad(d.getDate())}} ${{pad(d.getHours())}}:${{pad(d.getMinutes())}}`;
            }} catch {{ return String(ts); }}
        }}

        function escapeHtml(str) {{
            if (!str) return '';
            const div = document.createElement('div');
            div.textContent = String(str);
            return div.innerHTML;
        }}

        function prevPage() {{
            if (currentPage > 0) {{ currentPage--; loadTasks(); }}
        }}
        function nextPage() {{
            currentPage++;
            loadTasks();
        }}

        // --- 初始化 & 自动刷新 ---
        async function refreshAll() {{
            await Promise.all([
                loadOverview(), loadPlatforms(), loadCandidates(),
                loadTasks(), loadVolumes(), loadErrors()
            ]);
            document.getElementById('last-updated').textContent =
                '更新于 ' + new Date().toLocaleTimeString();
        }}

        function setupAutoRefresh() {{
            const cb = document.getElementById('auto-refresh');
            cb.addEventListener('change', () => {{
                if (cb.checked) {{
                    refreshTimer = setInterval(refreshAll, 60000);
                }} else {{
                    if (refreshTimer) clearInterval(refreshTimer);
                }}
            }});
            refreshTimer = setInterval(refreshAll, 60000);
        }}

        refreshAll();
        setupAutoRefresh();
    </script>

    <!-- 候选热度曲线弹窗 -->
    <div class="modal-overlay" id="candidate-modal" onclick="if(event.target===this)closeCandidateModal()">
        <div class="modal-box">
            <button class="modal-close" onclick="closeCandidateModal()">&times;</button>
            <h3 id="modal-title">-</h3>
            <div class="modal-sub" id="modal-sub">-</div>
            <div class="modal-chart">
                <canvas id="candidate-chart"></canvas>
            </div>
        </div>
    </div>
</body>
</html>"""
