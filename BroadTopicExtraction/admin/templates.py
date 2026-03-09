# -*- coding: utf-8 -*-
"""
前端 HTML/JS/CSS 模板

单页面 HTML，通过 JS fetch 调 API，Chart.js 画趋势图。
"""


def get_dashboard_html(token: str = "") -> str:
    """生成监控面板 HTML"""
    token_param = f"token={token}" if token else ""
    amp_token = f"&token={token}" if token else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MindSpider 浅层采集监控面板</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #f0f2f5; color: #333; padding: 20px;
        }}
        .header {{
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 20px;
        }}
        .header h1 {{ font-size: 22px; color: #1a1a2e; }}
        .header-right {{ display: flex; align-items: center; gap: 12px; font-size: 13px; color: #888; }}
        .header-right label {{ cursor: pointer; }}
        .header-right input[type="checkbox"] {{ margin-right: 4px; }}

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
        .card.green .value {{ color: #52c41a; }}
        .card.yellow .value {{ color: #faad14; }}
        .card.red .value {{ color: #ff4d4f; }}

        /* 源状态表 */
        .section {{ background: #fff; border-radius: 8px; padding: 20px; margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .section h2 {{ font-size: 16px; margin-bottom: 16px; color: #1a1a2e; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        th {{ background: #fafafa; padding: 10px 12px; text-align: left; border-bottom: 2px solid #f0f0f0; font-weight: 600; color: #666; }}
        td {{ padding: 10px 12px; border-bottom: 1px solid #f5f5f5; }}
        tr:hover {{ background: #fafafa; }}
        .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }}
        .dot-green {{ background: #52c41a; }}
        .dot-yellow {{ background: #faad14; }}
        .dot-red {{ background: #ff4d4f; }}
        .dot-gray {{ background: #d9d9d9; }}
        .clickable {{ cursor: pointer; color: #1890ff; }}
        .clickable:hover {{ text-decoration: underline; }}

        /* 趋势图 */
        .chart-container {{ position: relative; height: 300px; }}

        /* 错误日志 */
        .error-log {{
            max-height: 400px; overflow-y: auto; font-family: "SFMono-Regular", Consolas, monospace;
            font-size: 12px; line-height: 1.6;
        }}
        .error-log .log-entry {{ padding: 6px 0; border-bottom: 1px solid #f5f5f5; }}
        .error-log .log-time {{ color: #888; margin-right: 8px; }}
        .error-log .log-source {{ color: #1890ff; margin-right: 8px; font-weight: 600; }}
        .error-log .log-msg {{ color: #ff4d4f; }}
        .filter-bar {{ margin-bottom: 12px; display: flex; gap: 10px; align-items: center; }}
        .filter-bar input {{ padding: 6px 10px; border: 1px solid #d9d9d9; border-radius: 4px; font-size: 13px; width: 200px; }}
        .filter-bar button {{ padding: 6px 14px; border: 1px solid #d9d9d9; border-radius: 4px; background: #fff; cursor: pointer; font-size: 13px; }}
        .filter-bar button:hover {{ border-color: #1890ff; color: #1890ff; }}

        #last-updated {{ font-size: 12px; color: #aaa; }}

        @media (max-width: 768px) {{
            .summary {{ grid-template-columns: repeat(2, 1fr); }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>MindSpider 浅层采集监控</h1>
            <div style="margin-top: 6px; font-size: 13px;">
                <a href="javascript:void(0)" id="deep-dashboard-link" style="color:#1890ff; text-decoration:none;">深层面板 →</a>
            </div>
        </div>
        <div class="header-right">
            <span id="last-updated"></span>
            <label><input type="checkbox" id="auto-refresh" checked> 60s 自动刷新</label>
        </div>
    </div>

    <script>
        // 设置深层面板链接
        document.getElementById('deep-dashboard-link').href =
            'http://' + window.location.hostname + ':8777/dashboard/?' + '{token_param}';
    </script>

    <!-- 摘要卡片 -->
    <div class="summary" id="summary-cards">
        <div class="card"><div class="label">总数据源</div><div class="value" id="total-count">-</div></div>
        <div class="card green"><div class="label">健康</div><div class="value" id="healthy-count">-</div></div>
        <div class="card yellow"><div class="label">警告</div><div class="value" id="warning-count">-</div></div>
        <div class="card red"><div class="label">异常</div><div class="value" id="error-count">-</div></div>
    </div>

    <!-- 源状态表 -->
    <div class="section">
        <h2>数据源状态</h2>
        <table>
            <thead>
                <tr>
                    <th>状态</th>
                    <th>源名称</th>
                    <th>显示名</th>
                    <th>分类</th>
                    <th>类型</th>
                    <th>最后执行</th>
                    <th>数据量</th>
                    <th>耗时(s)</th>
                    <th>连续失败</th>
                </tr>
            </thead>
            <tbody id="status-table-body">
                <tr><td colspan="9" style="text-align:center; color:#aaa;">加载中...</td></tr>
            </tbody>
        </table>
    </div>

    <!-- 数据趋势图 -->
    <div class="section">
        <h2>数据产出趋势 (48h)</h2>
        <div class="chart-container">
            <canvas id="volume-chart"></canvas>
        </div>
    </div>

    <!-- 错误日志 -->
    <div class="section">
        <h2>错误日志</h2>
        <div class="filter-bar">
            <input type="text" id="error-source-filter" placeholder="按源名称过滤...">
            <button onclick="loadErrors()">过滤</button>
        </div>
        <div class="error-log" id="error-log">
            <div class="log-entry" style="color:#aaa;">加载中...</div>
        </div>
    </div>

    <script>
        const TOKEN = "{token_param}";
        const AMP_TOKEN = "{amp_token}";
        let volumeChart = null;
        let refreshTimer = null;

        // --- 数据加载 ---
        async function fetchJSON(url) {{
            const sep = url.includes('?') ? '&' : '?';
            const fullUrl = TOKEN ? url + sep + TOKEN : url;
            const resp = await fetch(fullUrl);
            if (!resp.ok) throw new Error(`HTTP ${{resp.status}}`);
            return resp.json();
        }}

        async function loadStatus() {{
            try {{
                const data = await fetchJSON('/api/status');
                renderSummary(data);
                renderStatusTable(data);
            }} catch (e) {{
                console.error('加载状态失败:', e);
            }}
        }}

        async function loadVolumes() {{
            try {{
                const data = await fetchJSON('/api/volumes?hours=48');
                renderChart(data);
            }} catch (e) {{
                console.error('加载趋势失败:', e);
            }}
        }}

        async function loadErrors() {{
            try {{
                const source = document.getElementById('error-source-filter').value;
                let url = '/api/errors?limit=50';
                if (source) url += '&source=' + encodeURIComponent(source);
                const data = await fetchJSON(url);
                renderErrors(data);
            }} catch (e) {{
                console.error('加载错误日志失败:', e);
            }}
        }}

        // --- 渲染 ---
        function renderSummary(sources) {{
            let healthy = 0, warning = 0, error = 0;
            for (const s of sources) {{
                if (s.consecutive_failures === 0) healthy++;
                else if (s.consecutive_failures <= 2) warning++;
                else error++;
            }}
            document.getElementById('total-count').textContent = sources.length;
            document.getElementById('healthy-count').textContent = healthy;
            document.getElementById('warning-count').textContent = warning;
            document.getElementById('error-count').textContent = error;
        }}

        function renderStatusTable(sources) {{
            const tbody = document.getElementById('status-table-body');
            if (!sources.length) {{
                tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; color:#aaa;">暂无数据（请先运行 scheduler）</td></tr>';
                return;
            }}
            let html = '';
            for (const s of sources) {{
                let dotClass = 'dot-gray';
                if (s.last_success === null || s.last_success === undefined) dotClass = 'dot-gray';
                else if (s.consecutive_failures === 0) dotClass = 'dot-green';
                else if (s.consecutive_failures <= 2) dotClass = 'dot-yellow';
                else dotClass = 'dot-red';

                const lastTime = s.last_started_at ? formatTime(s.last_started_at) : '-';
                const items = s.last_item_count !== null && s.last_item_count !== undefined ? s.last_item_count : '-';
                const duration = s.last_duration !== null && s.last_duration !== undefined ? s.last_duration.toFixed(1) : '-';
                const failures = s.consecutive_failures;

                html += `<tr>
                    <td><span class="dot ${{dotClass}}"></span></td>
                    <td class="clickable" onclick="showSourceDetail('${{s.source_name}}')">${{s.source_name}}</td>
                    <td>${{s.display_name}}</td>
                    <td>${{s.category}}</td>
                    <td>${{s.source_type}}</td>
                    <td>${{lastTime}}</td>
                    <td>${{items}}</td>
                    <td>${{duration}}</td>
                    <td style="color:${{failures >= 3 ? '#ff4d4f' : failures >= 1 ? '#faad14' : '#52c41a'}}; font-weight:600;">${{failures}}</td>
                </tr>`;
            }}
            tbody.innerHTML = html;
        }}

        function renderChart(data) {{
            const ctx = document.getElementById('volume-chart').getContext('2d');

            // 收集所有小时标签
            const allHours = new Set();
            for (const coll of Object.keys(data)) {{
                for (const pt of data[coll]) allHours.add(pt.hour);
            }}
            const labels = Array.from(allHours).sort();

            const colors = {{
                aggregator: '#1890ff',
                hot_national: '#52c41a',
                hot_vertical: '#faad14',
                media: '#722ed1',
            }};

            const datasets = [];
            for (const [coll, points] of Object.entries(data)) {{
                const countMap = {{}};
                for (const pt of points) countMap[pt.hour] = pt.count;
                datasets.push({{
                    label: coll,
                    data: labels.map(h => countMap[h] || 0),
                    borderColor: colors[coll] || '#999',
                    backgroundColor: (colors[coll] || '#999') + '20',
                    tension: 0.3,
                    fill: true,
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
                        y: {{ beginAtZero: true, title: {{ display: true, text: '文档数' }} }},
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
                const src = e.source_hint ? `<span class="log-source">${{e.source_hint}}</span>` : '';
                html += `<div class="log-entry">
                    <span class="log-time">${{e.time}}</span>
                    ${{src}}
                    <span class="log-msg">${{escapeHtml(e.message)}}</span>
                </div>`;
            }}
            container.innerHTML = html;
        }}

        // --- 工具函数 ---
        function formatTime(isoStr) {{
            if (!isoStr) return '-';
            try {{
                const d = new Date(isoStr);
                if (isNaN(d.getTime())) return isoStr;
                const pad = n => String(n).padStart(2, '0');
                return `${{pad(d.getMonth()+1)}}-${{pad(d.getDate())}} ${{pad(d.getHours())}}:${{pad(d.getMinutes())}}`;
            }} catch {{ return isoStr; }}
        }}

        function escapeHtml(str) {{
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }}

        async function showSourceDetail(sourceName) {{
            try {{
                const data = await fetchJSON('/api/source/' + encodeURIComponent(sourceName));
                let msg = `${{sourceName}} 最近执行记录:\\n\\n`;
                for (const r of data.slice(0, 10)) {{
                    const status = r.success ? 'OK' : 'FAIL';
                    const time = r.started_at ? formatTime(r.started_at) : '-';
                    const items = r.item_count !== null && r.item_count !== undefined ? r.item_count : '-';
                    const dur = r.duration_seconds !== null ? r.duration_seconds.toFixed(1) + 's' : '-';
                    const err = r.error_message ? '\\n    ' + r.error_message : '';
                    msg += `[${{status}}] ${{time}} | ${{items}} 条 | ${{dur}}${{err}}\\n`;
                }}
                alert(msg);
            }} catch (e) {{
                alert('加载失败: ' + e.message);
            }}
        }}

        // --- 初始化 & 自动刷新 ---
        async function refreshAll() {{
            await Promise.all([loadStatus(), loadVolumes(), loadErrors()]);
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

        // 启动
        refreshAll();
        setupAutoRefresh();
    </script>
</body>
</html>"""
