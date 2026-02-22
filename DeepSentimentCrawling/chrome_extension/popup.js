// ── 平台配置（扩容只需加一行）──────────────────────────────
const DOMAIN_MAP = {
  "douyin.com":       { code: "dy",    name: "抖音",     cookieDomain: ".douyin.com" },
  "xiaohongshu.com":  { code: "xhs",   name: "小红书",   cookieDomain: ".xiaohongshu.com" },
  "bilibili.com":     { code: "bili",  name: "B站",      cookieDomain: ".bilibili.com" },
  "weibo.com":        { code: "wb",    name: "微博",     cookieDomain: ".weibo.com" },
  "weibo.cn":         { code: "wb",    name: "微博",     cookieDomain: ".weibo.cn" },
  "kuaishou.com":     { code: "ks",    name: "快手",     cookieDomain: ".kuaishou.com" },
  "baidu.com":        { code: "tieba", name: "贴吧",     cookieDomain: ".baidu.com" },
  "zhihu.com":        { code: "zhihu", name: "知乎",     cookieDomain: ".zhihu.com" },
};

// ── DOM 引用 ────────────────────────────────────────────────
const $serverUrl       = document.getElementById("server-url");
const $token           = document.getElementById("token");
const $btnSaveSettings = document.getElementById("btn-save-settings");
const $settingsStatus  = document.getElementById("settings-status");
const $platformLabel   = document.getElementById("platform-label");
const $btnExport       = document.getElementById("btn-export");
const $result          = document.getElementById("result");
const $platformButtons = document.getElementById("platform-buttons");

// ── 初始化 ──────────────────────────────────────────────────
let currentPlatform = null;  // {code, name, cookieDomain} | null

chrome.storage.local.get(["serverUrl", "token"], (cfg) => {
  if (cfg.serverUrl) $serverUrl.value = cfg.serverUrl;
  if (cfg.token)     $token.value     = cfg.token;
});

// 检测当前 tab 所属平台
chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  if (!tabs[0]?.url) {
    $platformLabel.textContent = "无法识别当前页面";
    renderPlatformButtons();
    return;
  }
  try {
    const hostname = new URL(tabs[0].url).hostname;
    currentPlatform = matchPlatform(hostname);
  } catch {
    currentPlatform = null;
  }

  if (currentPlatform) {
    $platformLabel.textContent = `当前平台: ${currentPlatform.name} (${currentPlatform.code})`;
    $btnExport.disabled = false;
  } else {
    $platformLabel.textContent = "当前页面不属于已配置平台";
  }
  renderPlatformButtons();
});

// ── 事件 ────────────────────────────────────────────────────
$btnSaveSettings.addEventListener("click", () => {
  const serverUrl = $serverUrl.value.trim().replace(/\/+$/, "");
  const token     = $token.value.trim();
  if (!serverUrl) {
    showSettingsStatus("请输入服务器地址", true);
    return;
  }
  chrome.storage.local.set({ serverUrl, token }, () => {
    showSettingsStatus("已保存", false);
  });
});

$btnExport.addEventListener("click", () => {
  if (!currentPlatform) return;
  exportCookies(currentPlatform.code, currentPlatform.cookieDomain, $btnExport);
});

// ── 核心：导出 cookie ───────────────────────────────────────
async function exportCookies(platformCode, cookieDomain, triggerEl) {
  const cfg = await getConfig();
  if (!cfg.serverUrl) {
    showResult("请先设置服务器地址", true);
    return;
  }

  // UI 反馈
  if (triggerEl === $btnExport) {
    $btnExport.disabled = true;
    $btnExport.textContent = "导出中...";
  } else {
    triggerEl.className = "exporting";
    triggerEl.textContent = "...";
  }
  showResult("正在读取 Cookie...", false, true);

  try {
    // 读取 cookie
    const cookies = await chrome.cookies.getAll({ domain: cookieDomain });
    if (!cookies.length) {
      throw new Error("未找到 Cookie，请先在浏览器中登录该平台");
    }

    const cookieStr = cookies.map(c => `${c.name}=${c.value}`).join("; ");

    // 发送到服务器
    const url = `${cfg.serverUrl}/login/${platformCode}/paste?token=${encodeURIComponent(cfg.token || "")}`;
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cookie_str: cookieStr }),
    });

    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`HTTP ${resp.status}: ${text}`);
    }

    const data = await resp.json();
    if (data.status === "success") {
      showResult(`已导出 ${data.count} 个 Cookie`, false);
      if (triggerEl !== $btnExport) {
        triggerEl.className = "done";
        triggerEl.textContent = platformNameByCode(platformCode);
      }
    } else {
      throw new Error(data.message || "服务器返回错误");
    }
  } catch (err) {
    showResult(err.message, true);
    if (triggerEl !== $btnExport) {
      triggerEl.className = "fail";
      triggerEl.textContent = platformNameByCode(platformCode);
    }
  } finally {
    if (triggerEl === $btnExport) {
      $btnExport.disabled = false;
      $btnExport.textContent = "一键导出 Cookie";
    }
  }
}

// ── 平台快捷按钮 ────────────────────────────────────────────
function renderPlatformButtons() {
  const seen = new Set();
  for (const info of Object.values(DOMAIN_MAP)) {
    if (seen.has(info.code)) continue;
    seen.add(info.code);

    const btn = document.createElement("button");
    btn.textContent = info.name;
    btn.addEventListener("click", () => {
      exportCookies(info.code, info.cookieDomain, btn);
    });
    $platformButtons.appendChild(btn);
  }
}

// ── 工具函数 ────────────────────────────────────────────────
function matchPlatform(hostname) {
  for (const [domain, info] of Object.entries(DOMAIN_MAP)) {
    if (hostname === domain || hostname.endsWith("." + domain)) return info;
  }
  return null;
}

function getConfig() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["serverUrl", "token"], resolve);
  });
}

function platformNameByCode(code) {
  for (const info of Object.values(DOMAIN_MAP)) {
    if (info.code === code) return info.name;
  }
  return code;
}

function showResult(msg, isError, isLoading) {
  $result.textContent = msg;
  $result.className = isError ? "error" : isLoading ? "loading" : "success";
}

function showSettingsStatus(msg, isError) {
  $settingsStatus.textContent = msg;
  $settingsStatus.style.color = isError ? "#ff4d4f" : "#52c41a";
  setTimeout(() => { $settingsStatus.textContent = ""; }, 3000);
}
