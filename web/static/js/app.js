/**
 * Mandarake 【ドール】 专区爬虫与自动下单控制看板 - 前端交互逻辑
 */

// 全局应用状态
const state = {
    currentPage: 1,
    pageSize: 36,
    totalPages: 1,
    totalItems: 0,
    categoryCode: "020114",
    stockStatus: "", // ""=全部, "1"=在库, "0"=售罄
    keyword: "",
    excludeKeyword: "",
    minPrice: null,
    maxPrice: null,
    sortBy: "id",
    sortOrder: "desc",

    // 预设关键词与多标签组合
    presetKeywords: [],
    selectedPresets: [],
    presetMode: "or", // "and" 或 "or"

    selectedCategoriesForCrawl: [],
    lastLogId: 0,
    terminalLogs: [],
    activeTab: "tab-products",

    // 上新记录状态
    records: {
        page: 1,
        pageSize: 20,
        totalPages: 1,
        totalItems: 0,
        keyword: "",
        ruleName: "",
        unreadCount: 0
    },

    // 自动抢购与一键购买状态
    purchase: {
        isLoggedIn: false,
        accountName: "",
        dryRun: true,
        autoBuyEnabled: false,
        autoBuyScope: "rule_enabled",
        autoBuyOnRestock: true,
        autoBuyMaxPrice: 0,
        autoBuyMaxOrdersPerDay: 5,
        autoBuyCooldownSeconds: 3600,
        quickBuySkipConfirm: false,
        defaultPaymentMethod: "bank",
        defaultPaymentLabel: "銀行振込（银行转账 · 推荐）",
        fallbackPaymentMethod: "cod",
        useCustomAddress: false,
        address: {},
        paymentMethods: [],
        excludedPaymentMethods: [],
        prefectures: [],
        unreadOrders: 0,
        pendingItem: null,
        configLoaded: false,
    },
};

// 默认本地兜底缩略图（避免官网远端图标 302 导致裂图黑块）
const DEFAULT_ITEM_THUMB = "/static/icon-192.png";

// 预设关键词本地持久化 Key 与内置默认娃娃特征标签
const PRESET_STORAGE_KEY = "mandarake_preset_keywords";
const DEFAULT_PRESET_KEYWORDS = [
    "MDD", "DDP", "DD", "SDGr", "SD13", "SD17", "幼SD", "DearSD",
    "ブライス", "カスタムヘッド", "ウィッグ", "アイ", "アウトフィット", "難あり"
];

// 页面加载完成后初始化
document.addEventListener("DOMContentLoaded", () => {
    initTabs();
    initSSE();
    loadTerminalLogs();
    loadCategories();
    initPresetKeywords();
    loadProducts();
    loadStats();
    loadMonitorConfig();
    initMonitorRecords();
    initPurchaseModule();
    loadPurchaseConfig();
    initEventListeners();
    initMobileInteractions();
    
    // 定时轮询统计与状态 (每 10 秒)
    setInterval(() => {
        loadStats();
        if (state.activeTab === "tab-purchase") {
            loadPurchaseOrders();
        }
    }, 10000);
});

// --- 选项卡切换 ---
function initTabs() {
    const tabBtns = document.querySelectorAll(".tab-btn");
    tabBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            const targetId = btn.getAttribute("data-tab");
            tabBtns.forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

            btn.classList.add("active");
            const targetContent = document.getElementById(targetId);
            if (targetContent) {
                targetContent.classList.add("active");
            }
            state.activeTab = targetId;

            // 联动更新网页标题 (保证在浏览器标签页直观展示当前功能)
            const tabTitleMap = {
                "tab-products": "商品阅览 - Mandarake 【ドール】 专区",
                "tab-crawler": "分类爬虫 - Mandarake 【ドール】 专区",
                "tab-monitor": "上新监控 - Mandarake 【ドール】 专区",
                "tab-monitor-records": "上新记录 - Mandarake 【ドール】 专区",
                "tab-purchase": "抢购设置 - Mandarake 【ドール】 专区",
                "tab-terminal": "实时终端 - Mandarake 【ドール】 专区"
            };
            if (tabTitleMap[targetId]) {
                document.title = tabTitleMap[targetId];
            }

            if (targetId === "tab-products") {
                loadProducts();
            } else if (targetId === "tab-crawler") {
                loadCrawlerStatus();
            } else if (targetId === "tab-monitor") {
                loadMonitorConfig();
            } else if (targetId === "tab-monitor-records") {
                loadMonitorRecords(1);
                loadRecordsStats();
                const badge = document.getElementById("monitor-records-badge");
                if (badge) {
                    state.records.unreadCount = 0;
                    badge.style.display = "none";
                }
            } else if (targetId === "tab-purchase") {
                loadPurchaseConfig();
                loadPurchaseOrders();
                clearPurchaseOrdersBadge();
            } else if (targetId === "tab-terminal") {
                renderTerminalLogs();
                const termBody = document.getElementById("terminal-output-container");
                const autoScrollCheck = document.getElementById("terminal-autoscroll");
                if (termBody && autoScrollCheck && autoScrollCheck.checked) {
                    termBody.scrollTop = termBody.scrollHeight;
                }
            }
        });
    });

    // 模态弹窗内的子 Tab 切换
    const subtabBtns = document.querySelectorAll(".subtab-btn");
    subtabBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            const targetId = btn.getAttribute("data-subtab");
            subtabBtns.forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".subtab-pane").forEach(p => p.classList.remove("active"));

            btn.classList.add("active");
            const pane = document.getElementById(targetId);
            if (pane) pane.classList.add("active");
        });
    });
}

// --- 事件监听器绑定 ---
function initEventListeners() {
    // 1. 在库状态分段按钮
    const segBtns = document.querySelectorAll("#stock-filter-group .seg-btn");
    segBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            segBtns.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            state.stockStatus = btn.getAttribute("data-val");
            state.currentPage = 1;
            loadProducts();
        });
    });

    // 2. 分类下拉筛选
    const catSelect = document.getElementById("filter-category");
    if (catSelect) {
        catSelect.addEventListener("change", (e) => {
            state.categoryCode = e.target.value;
            state.currentPage = 1;
            loadProducts();
        });
    }

    // 3. 排序下拉
    const sortSelect = document.getElementById("filter-sort");
    if (sortSelect) {
        sortSelect.addEventListener("change", (e) => {
            const [by, order] = e.target.value.split(":");
            state.sortBy = by;
            state.sortOrder = order || "desc";
            state.currentPage = 1;
            loadProducts();
        });
    }

    // 4. 应用筛选按钮
    document.getElementById("btn-apply-filter")?.addEventListener("click", () => {
        applyFilterInputs();
        state.currentPage = 1;
        loadProducts();
    });

    // 回车直接触发筛选
    document.getElementById("filter-keyword")?.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            applyFilterInputs();
            state.currentPage = 1;
            loadProducts();
        }
    });

    // 5. 清空搜索
    document.getElementById("btn-clear-keyword")?.addEventListener("click", () => {
        const input = document.getElementById("filter-keyword");
        if (input) input.value = "";
        state.keyword = "";
        state.currentPage = 1;
        loadProducts();
    });

    // 6. 重置全部筛选
    document.getElementById("btn-reset-filter")?.addEventListener("click", () => {
        state.categoryCode = "020114";
        state.stockStatus = "";
        state.keyword = "";
        state.excludeKeyword = "";
        state.minPrice = null;
        state.maxPrice = null;
        state.sortBy = "id";
        state.sortOrder = "desc";
        state.currentPage = 1;
        state.selectedPresets = [];
        renderPresetKeywords();

        if (catSelect) catSelect.value = "020114";
        segBtns.forEach(b => b.classList.toggle("active", b.getAttribute("data-val") === ""));
        const kwInput = document.getElementById("filter-keyword");
        if (kwInput) kwInput.value = "";
        const exInput = document.getElementById("filter-exclude");
        if (exInput) exInput.value = "";
        const minP = document.getElementById("filter-min-price");
        if (minP) minP.value = "";
        const maxP = document.getElementById("filter-max-price");
        if (maxP) maxP.value = "";
        if (sortSelect) sortSelect.value = "id:desc";

        loadProducts();
    });

    // 智能分类归整按钮 (后台分类爬取控制页)
    document.getElementById("btn-reclassify-cats")?.addEventListener("click", async () => {
        const btn = document.getElementById("btn-reclassify-cats");
        const statusEl = document.getElementById("reclassify-status-text");
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> 正在扫描并规整细分商品...`;
        }
        if (statusEl) {
            statusEl.innerHTML = `<span style="color: #38bdf8;"><i class="fa-solid fa-spinner fa-spin"></i> 正在扫描全库商品标题并自动归整三级子类，请稍候...</span>`;
        }
        try {
            const res = await fetch("/api/categories/reclassify", { method: "POST" });
            const data = await res.json();
            alert(data.message || "分类规整完成！");
            if (statusEl) {
                statusEl.innerHTML = `<span style="color: #10b981;"><i class="fa-solid fa-circle-check"></i> ${data.message || "分类规整完成！"}</span>`;
            }
            loadProducts();
            loadStats();
            loadCategories();
        } catch (err) {
            alert("智能分类规整异常: " + err);
            if (statusEl) {
                statusEl.innerHTML = `<span style="color: #ef4444;"><i class="fa-solid fa-triangle-exclamation"></i> 智能分类规整异常: ${err}</span>`;
            }
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = `<i class="fa-solid fa-wand-magic-sparkles"></i> 智能归整细分全库商品`;
            }
        }
    });

    // 7. 快捷前往爬虫页 / 抢购设置页
    document.getElementById("btn-quick-goto-crawler")?.addEventListener("click", () => {
        document.querySelector("[data-tab='tab-crawler']")?.click();
    });
    document.getElementById("purchase-header-pill")?.addEventListener("click", () => {
        document.querySelector("[data-tab='tab-purchase']")?.click();
    });

    // 14. 爬虫控制按纽
    document.getElementById("btn-start-crawl")?.addEventListener("click", startCrawl);
    document.getElementById("btn-pause-crawl")?.addEventListener("click", async () => {
        await fetch("/api/crawler/pause", { method: "POST" });
        loadCrawlerStatus();
    });
    document.getElementById("btn-resume-crawl")?.addEventListener("click", async () => {
        await fetch("/api/crawler/resume", { method: "POST" });
        loadCrawlerStatus();
    });
    document.getElementById("btn-stop-crawl")?.addEventListener("click", async () => {
        if (confirm("确定中止当前的爬虫任务吗？")) {
            await fetch("/api/crawler/stop", { method: "POST" });
            loadCrawlerStatus();
        }
    });
    document.getElementById("btn-trigger-daily-sync")?.addEventListener("click", async () => {
        const btn = document.getElementById("btn-trigger-daily-sync");
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> 正在触发全量巡检...`;
        }
        try {
            const res = await fetch("/api/stock/sync_daily", { method: "POST" });
            const data = await res.json();
            alert(data.message || (data.success ? "已启动每日规格库存巡检" : "启动失败"));
            loadStats();
        } catch (err) {
            alert("请求异常: " + err);
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = `<i class="fa-solid fa-rotate"></i> 即时触发每日05:00库存与售罄巡检`;
            }
        }
    });

    // 15. 清空数据库
    document.getElementById("btn-clear-db")?.addEventListener("click", async () => {
        if (confirm("⚠️ 确定要清空全部已归档的商品数据库吗？此操作无法恢复！")) {
            const res = await fetch("/api/archive/clear", { method: "POST" });
            const data = await res.json();
            alert(data.message);
            loadProducts();
            loadStats();
        }
    });

    // 17. 终端控制与过滤
    document.getElementById("btn-clear-terminal")?.addEventListener("click", async () => {
        await fetch("/api/terminal/clear", { method: "POST" });
        state.terminalLogs = [];
        renderTerminalLogs();
    });

    document.getElementById("terminal-level-select")?.addEventListener("change", () => {
        renderTerminalLogs();
    });

    let searchDebounceTimer = null;
    document.getElementById("terminal-search")?.addEventListener("input", () => {
        clearTimeout(searchDebounceTimer);
        searchDebounceTimer = setTimeout(() => {
            renderTerminalLogs();
        }, 150);
    });

    // 18. 监控频率与重试参数保存
    document.getElementById("btn-save-schedule-cfg")?.addEventListener("click", async () => {
        const rawInterval = parseFloat(document.getElementById("cfg-interval-seconds")?.value);
        const intervalSec = Number.isFinite(rawInterval) ? Math.max(0.1, Math.round(rawInterval * 100) / 100) : 15;
        const parsedRetryCount = parseInt(document.getElementById("cfg-retry-count")?.value, 10);
        const retryCount = Number.isNaN(parsedRetryCount) ? 3 : Math.max(0, parsedRetryCount);
        const rawRetryDelay = parseFloat(document.getElementById("cfg-retry-delay")?.value);
        const retryDelay = Number.isFinite(rawRetryDelay) ? Math.max(0.1, Math.round(rawRetryDelay * 100) / 100) : 2;
        const burstDuration = parseInt(document.getElementById("cfg-burst-duration")?.value, 10) || 3;
        const rawBurstInterval = parseFloat(document.getElementById("cfg-burst-interval")?.value);
        const burstInterval = Number.isFinite(rawBurstInterval) ? Math.max(0.1, Math.round(rawBurstInterval * 100) / 100) : 3;
        const rawBurstRetryDelay = parseFloat(document.getElementById("cfg-burst-retry-delay")?.value);
        const burstRetryDelay = Number.isFinite(rawBurstRetryDelay) ? Math.max(0.1, Math.round(rawBurstRetryDelay * 100) / 100) : 1;
        const bypassCache = document.getElementById("cfg-bypass-cache")?.checked !== false;
        const autoBurstOnHit = document.getElementById("cfg-auto-burst-on-hit")?.checked !== false;
        const monitorDispCount = parseInt(document.getElementById("cfg-monitor-disp-count")?.value, 10) || 48;

        try {
            const res = await fetch("/api/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    schedule: {
                        interval_seconds: intervalSec,
                        retry_count: retryCount,
                        retry_delay: retryDelay,
                        burst_duration_minutes: burstDuration,
                        burst_interval_seconds: burstInterval,
                        burst_retry_delay: burstRetryDelay,
                        bypass_cache: bypassCache,
                        auto_burst_on_hit: autoBurstOnHit,
                        monitor_disp_count: monitorDispCount
                    }
                })
            });
            const data = await res.json();
            const savedSched = (data && data.config && data.config.schedule) || {};
            const actualInterval = savedSched.interval_seconds !== undefined ? savedSched.interval_seconds : intervalSec;
            const actualBurstInterval = savedSched.burst_interval_seconds !== undefined ? savedSched.burst_interval_seconds : burstInterval;
            const intervalEl = document.getElementById("cfg-interval-seconds");
            if (intervalEl) intervalEl.value = actualInterval;
            const burstEl = document.getElementById("cfg-burst-interval");
            if (burstEl) burstEl.value = actualBurstInterval;
            const retryDelayEl = document.getElementById("cfg-retry-delay");
            if (retryDelayEl && savedSched.retry_delay !== undefined) retryDelayEl.value = savedSched.retry_delay;

            showToast(
                `调度参数已保存：日常间隔 ${actualInterval}s | 爆发间隔 ${actualBurstInterval}s (持续 ${burstDuration} 分钟)`,
                "success"
            );
            loadStats();
        } catch (err) {
            alert("保存失败: " + err);
        }
    });

    // 19. 邮箱配置保存与测试
    document.getElementById("btn-save-email-cfg")?.addEventListener("click", async () => {
        const enabled = document.getElementById("cfg-email-enabled")?.checked || false;
        const host = document.getElementById("cfg-email-host")?.value.trim() || "";
        const port = parseInt(document.getElementById("cfg-email-port")?.value, 10) || 465;
        const ssl = document.getElementById("cfg-email-ssl")?.checked || false;
        const user = document.getElementById("cfg-email-user")?.value.trim() || "";
        const pwd = document.getElementById("cfg-email-password")?.value.trim() || "";
        const receiver = document.getElementById("cfg-email-receiver")?.value.trim() || "";

        try {
            await fetch("/api/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    notifiers: {
                        email: {
                            enabled: enabled,
                            smtp_host: host,
                            smtp_port: port,
                            use_ssl: ssl,
                            smtp_user: user,
                            smtp_password: pwd,
                            receiver_email: receiver
                        }
                    }
                })
            });
            alert("邮箱通知设置已保存！状态: " + (enabled ? "已启用" : "未启用"));
        } catch (err) {
            alert("保存邮箱配置异常: " + err);
        }
    });

    document.getElementById("btn-test-email")?.addEventListener("click", async () => {
        const host = document.getElementById("cfg-email-host")?.value.trim() || "";
        const port = parseInt(document.getElementById("cfg-email-port")?.value, 10) || 465;
        const ssl = document.getElementById("cfg-email-ssl")?.checked || false;
        const user = document.getElementById("cfg-email-user")?.value.trim() || "";
        const pwd = document.getElementById("cfg-email-password")?.value.trim() || "";
        const receiver = document.getElementById("cfg-email-receiver")?.value.trim() || "";

        if (!host || !user || !pwd) {
            alert("请先完整填写 SMTP 服务器地址、邮箱账号和授权码/密码");
            return;
        }

        const testBtn = document.getElementById("btn-test-email");
        if (testBtn) {
            testBtn.disabled = true;
            testBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> 正在发送测试邮件...`;
        }

        try {
            const res = await fetch("/api/test_notify/email", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    smtp_host: host,
                    smtp_port: port,
                    use_ssl: ssl,
                    smtp_user: user,
                    smtp_password: pwd,
                    receiver_email: receiver
                })
            });
            const data = await res.json();
            alert(data.message);
        } catch (err) {
            alert("测试邮件请求异常: " + err);
        } finally {
            if (testBtn) {
                testBtn.disabled = false;
                testBtn.innerHTML = `<i class="fa-solid fa-paper-plane"></i> 发送测试邮件`;
            }
        }
    });

    // 20. Discord Webhook 保存与测试
    document.getElementById("btn-save-discord-cfg")?.addEventListener("click", async () => {
        const enabled = document.getElementById("cfg-discord-enabled")?.checked || false;
        const webhook = document.getElementById("cfg-discord-webhook")?.value.trim() || "";

        try {
            await fetch("/api/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    notifiers: {
                        discord: {
                            enabled: enabled,
                            webhook_url: webhook
                        }
                    }
                })
            });
            alert("Discord Webhook 设置已保存！状态: " + (enabled ? "已启用" : "未启用"));
        } catch (err) {
            alert("保存 Discord 配置异常: " + err);
        }
    });

    document.getElementById("btn-test-discord")?.addEventListener("click", async () => {
        const webhook = document.getElementById("cfg-discord-webhook")?.value.trim() || "";
        if (!webhook) {
            alert("请先填写有效的 Discord Webhook URL");
            return;
        }

        try {
            const res = await fetch("/api/test_notify/discord", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ webhook_url: webhook })
            });
            const data = await res.json();
            alert(data.message);
        } catch (err) {
            alert("测试 Discord Webhook 异常: " + err);
        }
    });

    // 22. 监控调度顶部控制
    document.getElementById("btn-monitor-check-now")?.addEventListener("click", async () => {
        const btn = document.getElementById("btn-monitor-check-now");
        if (btn) btn.disabled = true;
        try {
            const res = await fetch("/api/monitor/check_now", { method: "POST" });
            const data = await res.json();
            showToast(`巡检完成！发现并命中 ${data.matches_count || 0} 件新上架目标商品！`, "success");
            loadStats();
            loadProducts();
            loadRecordsStats();
            if (state.activeTab === "tab-monitor-records") {
                loadMonitorRecords(1);
            }
        } catch (err) {
            showToast("执行巡检失败: " + err, "error");
        } finally {
            if (btn) btn.disabled = false;
        }
    });

    document.getElementById("btn-monitor-burst")?.addEventListener("click", async () => {
        try {
            const burstDurationInput = document.getElementById("cfg-burst-duration");
            const duration = burstDurationInput ? parseInt(burstDurationInput.value, 10) : 3;
            const res = await fetch("/api/monitor/trigger_burst", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action: "toggle", duration: duration })
            });
            const data = await res.json();
            alert(data.message);
            loadStats();
        } catch (err) {
            alert("操作高频爆发模式失败: " + err);
        }
    });

    document.getElementById("btn-monitor-baseline")?.addEventListener("click", async () => {
        if (!confirm("确定将当前商城所有已有在售商品记录为基准线吗？初次启动建议执行，可避免把已有库存当成新上架报警。")) return;
        try {
            const res = await fetch("/api/monitor/init_baseline", { method: "POST" });
            const data = await res.json();
            alert(data.message);
        } catch (err) {
            alert("建立基准线失败: " + err);
        }
    });

    document.getElementById("btn-monitor-pause")?.addEventListener("click", async () => {
        const btn = document.getElementById("btn-monitor-pause");
        if (btn) btn.disabled = true;
        try {
            const res = await fetch("/api/monitor/toggle_pause", { method: "POST" });
            const data = await res.json();
            if (data.success) {
                showToast(data.message || (data.paused ? "已暂停后台监控调度" : "已恢复后台监控调度"), data.paused ? "warning" : "success");
                if (data.scheduler) {
                    updateSchedulerUI(data.scheduler);
                }
            } else {
                showToast(data.message || "切换监控状态失败", "error");
            }
            loadStats();
        } catch (err) {
            showToast("操作失败: " + err, "error");
        } finally {
            if (btn) btn.disabled = false;
        }
    });

    // 23. 监控规则模态框
    const ruleModal = document.getElementById("rule-modal");
    document.getElementById("btn-open-add-rule")?.addEventListener("click", () => {
        openRuleModal();
    });
    document.getElementById("btn-close-rule-modal")?.addEventListener("click", () => {
        ruleModal?.classList.remove("active");
    });
    document.getElementById("btn-cancel-rule-modal")?.addEventListener("click", () => {
        ruleModal?.classList.remove("active");
    });

    // 匹配模式切换 (AND / OR)
    const ruleModeBtns = document.querySelectorAll("#modal-rule-mode-group .seg-btn");
    ruleModeBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            ruleModeBtns.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
        });
    });

    // 保存规则
    document.getElementById("btn-save-rule-modal")?.addEventListener("click", async () => {
        const idEl = document.getElementById("modal-rule-id");
        const id = idEl?.value.trim() || "";
        const name = document.getElementById("modal-rule-name")?.value.trim() || "";
        const catCode = document.getElementById("modal-rule-cat")?.value || "020114";
        const kwStr = document.getElementById("modal-rule-keywords")?.value.trim() || "";
        const exStr = document.getElementById("modal-rule-exclude")?.value.trim() || "";
        const maxPrice = parseInt(document.getElementById("modal-rule-max-price")?.value, 10) || 0;
        const activeModeBtn = document.querySelector("#modal-rule-mode-group .seg-btn.active");
        const matchMode = activeModeBtn ? activeModeBtn.getAttribute("data-mode") : "and";
        const autoBuy = Boolean(document.getElementById("modal-rule-auto-buy")?.checked);

        if (!name) {
            alert("请填写规则名称");
            return;
        }

        const keywords = splitKeywordsInput(kwStr);
        const excludeKeywords = splitKeywordsInput(exStr);
        const isEdit = Boolean(id);
        const enabledVal = isEdit && idEl?.dataset.enabled !== undefined
            ? idEl.dataset.enabled !== "false"
            : true;

        try {
            const res = await fetch("/api/monitor/rules/save", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    id: id || undefined,
                    name: name,
                    category_code: catCode,
                    keywords: keywords,
                    exclude_keywords: excludeKeywords,
                    match_mode: matchMode,
                    max_price: maxPrice,
                    auto_buy: autoBuy,
                    enabled: enabledVal
                })
            });
            const data = await res.json();
            if (!data.success) {
                alert("保存规则失败: " + (data.message || "未知错误"));
                return;
            }
            ruleModal?.classList.remove("active");
            alert(data.message);
            renderRulesTable(data.rules || []);
        } catch (err) {
            alert("保存规则失败: " + err);
        }
    });
}

function applyFilterInputs() {
    const kwInput = document.getElementById("filter-keyword");
    state.keyword = kwInput ? kwInput.value.trim() : "";

    const exInput = document.getElementById("filter-exclude");
    state.excludeKeyword = exInput ? exInput.value.trim() : "";

    const minP = document.getElementById("filter-min-price");
    state.minPrice = (minP && minP.value) ? parseInt(minP.value, 10) : null;

    const maxP = document.getElementById("filter-max-price");
    state.maxPrice = (maxP && maxP.value) ? parseInt(maxP.value, 10) : null;

    updateActiveFilterCount();
}

// --- 分类体系加载与下拉树渲染 ---
async function loadCategories() {
    try {
        const res = await fetch("/api/categories");
        const data = await res.json();
        if (!data.success) return;

        const select = document.getElementById("filter-category");
        const crawlBox = document.getElementById("crawler-cat-selection-list");
        if (!select) return;

        select.innerHTML = "";
        if (crawlBox) crawlBox.innerHTML = "";
        const ruleCatSelect = document.getElementById("modal-rule-cat");
        if (ruleCatSelect) ruleCatSelect.innerHTML = "";

        const counts = data.counts || {};

        // 扁平列表渲染下拉框
        data.flat_list.forEach(cat => {
            const opt = document.createElement("option");
            opt.value = cat.code;
            const indent = "　".repeat(Math.max(0, cat.level - 1));
            const cnt = counts[cat.code] !== undefined ? counts[cat.code] : 0;
            opt.textContent = `${indent}${cat.name} (${cat.code}) [${cnt} 件]`;
            select.appendChild(opt);

            if (ruleCatSelect) {
                const rOpt = document.createElement("option");
                rOpt.value = cat.code;
                rOpt.textContent = `${indent}${cat.name} (${cat.code})`;
                ruleCatSelect.appendChild(rOpt);
            }

            // 若为二级分类，同时加入爬虫多选面板
            if (crawlBox && cat.level === 2) {
                const label = document.createElement("label");
                label.className = "checkbox-label";
                label.innerHTML = `
                    <input type="checkbox" class="crawler-cat-check" value="${cat.code}" checked>
                    <span>${cat.name} <small class="text-muted">(${cat.code} · 已存 ${cnt} 件)</small></span>
                `;
                crawlBox.appendChild(label);
            }
        });

        if (state.categoryCode) {
            select.value = state.categoryCode;
        }

        // 爬虫多选全选/清空
        document.getElementById("btn-select-all-cats")?.addEventListener("click", () => {
            document.querySelectorAll(".crawler-cat-check").forEach(cb => cb.checked = true);
        });
        document.getElementById("btn-unselect-all-cats")?.addEventListener("click", () => {
            document.querySelectorAll(".crawler-cat-check").forEach(cb => cb.checked = false);
        });

    } catch (err) {
        console.error("加载分类数据失败:", err);
    }
}

// --- 商品档案查询与卡片渲染 ---
async function loadProducts() {
    const container = document.getElementById("products-container");
    if (!container) return;

    container.innerHTML = `
        <div class="empty-placeholder">
            <i class="fa-solid fa-spinner fa-spin empty-icon"></i>
            <p>正在高效检索 Mandarake 【ドール】 档案库...</p>
        </div>
    `;

    const params = new URLSearchParams({
        page: state.currentPage,
        page_size: state.pageSize,
        category_code: state.categoryCode,
        sort_by: state.sortBy,
        sort_order: state.sortOrder,
    });

    if (state.stockStatus !== "") params.append("is_in_stock", state.stockStatus);
    if (state.keyword) params.append("keyword", state.keyword);
    if (state.excludeKeyword) params.append("exclude_keyword", state.excludeKeyword);
    if (state.selectedPresets && state.selectedPresets.length > 0) {
        params.append("preset_keywords", state.selectedPresets.join(","));
        params.append("preset_mode", state.presetMode);
    }
    if (state.minPrice !== null && !isNaN(state.minPrice)) params.append("min_price", state.minPrice);
    if (state.maxPrice !== null && !isNaN(state.maxPrice)) params.append("max_price", state.maxPrice);

    try {
        const res = await fetch(`/api/products?${params.toString()}`);
        const data = await res.json();
        if (!data.success) {
            container.innerHTML = `<div class="empty-placeholder"><p>查询失败: ${data.message || '未知错误'}</p></div>`;
            return;
        }

        state.totalItems = data.total;
        state.totalPages = data.total_pages;

        // 更新统计指示
        const totalCountEl = document.getElementById("query-total-count");
        if (totalCountEl) totalCountEl.textContent = data.total.toLocaleString();

        const summaryEl = document.getElementById("query-condition-summary");
        if (summaryEl) {
            const conds = [];
            if (state.stockStatus === "1") conds.push("仅在库");
            if (state.stockStatus === "0") conds.push("仅售罄");
            if (state.keyword) conds.push(`关键词:"${state.keyword}"`);
            if (state.selectedPresets && state.selectedPresets.length > 0) {
                const modeLabel = state.presetMode.toUpperCase();
                conds.push(`预设标签[${modeLabel}]: ${state.selectedPresets.join("、")}`);
            }
            summaryEl.textContent = conds.length > 0 ? `(${conds.join(", ")})` : "";
        }

        renderPagination();

        if (data.items.length === 0) {
            container.innerHTML = `
                <div class="empty-placeholder">
                    <i class="fa-solid fa-box-open empty-icon"></i>
                    <p>未找到符合条件的商品。您可以调整筛选条件，或前往【后台分类爬取】启动抓取最新商品！</p>
                    <button class="btn btn-primary" onclick="document.querySelector('[data-tab=\\'tab-crawler\\']').click()">前往启动爬虫</button>
                </div>
            `;
            return;
        }

        container.innerHTML = "";
        data.items.forEach(item => {
            const card = createProductCard(item);
            container.appendChild(card);
        });

    } catch (err) {
        container.innerHTML = `<div class="empty-placeholder"><p>加载商品列表异常: ${err}</p></div>`;
    }
}

// 创建单个商品卡片 DOM (点击整卡在新标签页直达 Mandarake 官网详情)
function createProductCard(item) {
    const card = document.createElement("div");
    const isInStock = item.is_in_stock === 1;
    const isNew = item.is_new === 1;
    
    card.className = "product-card clickable-card" + (isInStock ? "" : " is-soldout");
    card.setAttribute("data-item-id", item.id);
    card.setAttribute("title", "点击在新标签页打开 Mandarake 官网查看商品详情");

    // 缩略图 fallback
    const imgUrl = item.img_url || DEFAULT_ITEM_THUMB;
    const priceText = item.price ? `${item.price.toLocaleString()}円` : "价格未标明";
    const shopText = item.shop || "まんだらけ";
    const itemUrl = item.url || `https://order.mandarake.co.jp/order/detailPage/item?itemCode=${item.id}`;

    card.innerHTML = `
        <div class="card-media-wrap">
            <img src="${escapeHtml(imgUrl)}" alt="${escapeHtml(item.title)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.onerror=null;this.src='/static/icon-192.png'">
            <div class="card-badges">
                ${isInStock ? '<span class="badge-tag stock">🟢 在库有货</span>' : '<span class="badge-tag soldout">🔴 売り切れ</span>'}
                ${isNew ? '<span class="badge-tag new-arrival">⚡ 新着</span>' : ''}
            </div>
            <div class="shop-tag"><i class="fa-solid fa-store"></i> ${escapeHtml(shopText)}</div>
            <div class="card-direct-link-hint">
                <i class="fa-solid fa-arrow-up-right-from-square"></i> 直达官网
            </div>
        </div>
        <div class="card-content">
            <div class="card-cat-path" title="${escapeHtml(item.category_name || '')}">
                <i class="fa-solid fa-tag"></i> ${escapeHtml(item.category_name || 'ドール')}
            </div>
            <h4 class="card-title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</h4>
            <div class="card-price-row">
                <div class="price-main">${priceText}</div>
                <div class="price-tax">${item.price_str ? escapeHtml(item.price_str) : ''}</div>
            </div>
            <div class="card-actions-bar">
                <button type="button" class="btn-card-quick-buy" ${isInStock ? "" : "disabled"} title="${isInStock ? '立即一键抢购下单' : '该商品已售罄'}">
                    <i class="fa-solid fa-bolt"></i> ${isInStock ? '一键抢购' : '已售罄'}
                </button>
                <button type="button" class="btn-card-open-site" title="在新标签页打开官网详情">
                    <i class="fa-solid fa-arrow-up-right-from-square"></i> 官网
                </button>
            </div>
        </div>
    `;

    // 一键抢购按钮事件（阻止冒泡到外层跳转）
    const quickBuyBtn = card.querySelector(".btn-card-quick-buy");
    if (quickBuyBtn && isInStock) {
        quickBuyBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            handleOneClickBuy(item, quickBuyBtn);
        });
    }

    const openSiteBtn = card.querySelector(".btn-card-open-site");
    if (openSiteBtn) {
        openSiteBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            window.open(itemUrl, "_blank", "noopener,noreferrer");
        });
    }

    // 点击卡片其余区域打开官网详情页
    card.addEventListener("click", (e) => {
        if (e.target.closest(".card-actions-bar")) return;
        window.open(itemUrl, "_blank", "noopener,noreferrer");
    });

    return card;
}

// 渲染分页按钮
function renderPagination() {
    const topContainer = document.getElementById("top-pagination");
    const bottomContainer = document.getElementById("bottom-pagination");

    const html = generatePaginationHtml(state.currentPage, state.totalPages);
    if (topContainer) topContainer.innerHTML = html;
    if (bottomContainer) bottomContainer.innerHTML = html;

    // 绑定分页点击事件
    document.querySelectorAll(".page-btn[data-page]").forEach(btn => {
        btn.addEventListener("click", () => {
            const p = parseInt(btn.getAttribute("data-page"), 10);
            if (p && p !== state.currentPage && p >= 1 && p <= state.totalPages) {
                state.currentPage = p;
                loadProducts();
                window.scrollTo({ top: 0, behavior: "smooth" });
            }
        });
    });
}

function generatePaginationHtml(current, total) {
    if (total <= 1) return "";

    let html = `<button class="page-btn" data-page="${current - 1}" ${current <= 1 ? 'disabled' : ''}>上一页</button>`;

    // 经典缩略分页算法
    let pages = [];
    if (total <= 7) {
        for (let i = 1; i <= total; i++) pages.push(i);
    } else {
        pages.push(1);
        if (current > 3) pages.push("...");
        for (let i = Math.max(2, current - 1); i <= Math.min(total - 1, current + 1); i++) {
            pages.push(i);
        }
        if (current < total - 2) pages.push("...");
        pages.push(total);
    }

    pages.forEach(p => {
        if (p === "...") {
            html += `<span class="page-ellipsis" style="color:var(--text-muted);padding:0 4px;">...</span>`;
        } else {
            html += `<button class="page-btn ${p === current ? 'active' : ''}" data-page="${p}">${p}</button>`;
        }
    });

    html += `<button class="page-btn" data-page="${current + 1}" ${current >= total ? 'disabled' : ''}>下一页</button>`;
    return html;
}

// --- 统计概览刷新 ---
async function loadStats() {
    try {
        const res = await fetch("/api/stats");
        const data = await res.json();
        if (!data.success) return;

        const s = data.stats;
        document.getElementById("stat-total").textContent = (s.total_products || 0).toLocaleString();
        document.getElementById("stat-stock").textContent = (s.in_stock_products || 0).toLocaleString();
        document.getElementById("stat-sold").textContent = (s.soldout_products || 0).toLocaleString();
        document.getElementById("stat-new").textContent = (s.new_arrival_products || 0).toLocaleString();

        // 爬虫状态同步
        updateCrawlerUI(data.crawler);

        // 调度器与上新监控状态同步
        if (data.scheduler) {
            updateSchedulerUI(data.scheduler);
        }

        // 抢购引擎状态同步
        if (data.purchase) {
            updatePurchaseStatusUI(data.purchase);
        }

        } catch (err) {
        console.debug("获取统计失败:", err);
    }
}

// --- 爬虫控制与状态 ---
async function startCrawl() {
    const selectedCats = [];
    document.querySelectorAll(".crawler-cat-check:checked").forEach(cb => {
        selectedCats.push(cb.value);
    });

    const stockOnly = document.getElementById("crawl-stock-only")?.checked ?? false;
    const maxPagesInput = document.getElementById("crawl-max-pages");
    const maxPages = maxPagesInput && maxPagesInput.value ? parseInt(maxPagesInput.value, 10) : null;

    try {
        const res = await fetch("/api/crawler/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                category_codes: selectedCats,
                in_stock_only: stockOnly,
                max_pages: maxPages
            })
        });
        const data = await res.json();
        alert(data.message);
        loadCrawlerStatus();
    } catch (err) {
        alert("启动爬虫异常: " + err);
    }
}

async function loadCrawlerStatus() {
    try {
        const res = await fetch("/api/crawler/status");
        const data = await res.json();
        if (data.success) {
            updateCrawlerUI(data.status);
        }
        loadCrawlProgressTable();
    } catch (err) {
        console.debug("获取爬虫状态失败:", err);
    }
}

function updateCrawlerUI(status) {
    if (!status) return;

    const startBtn = document.getElementById("btn-start-crawl");
    const pauseBtn = document.getElementById("btn-pause-crawl");
    const resumeBtn = document.getElementById("btn-resume-crawl");
    const stopBtn = document.getElementById("btn-stop-crawl");
    const liveTag = document.getElementById("crawler-live-tag");
    const statusMsg = document.getElementById("crawl-status-msg");

    if (status.is_running) {
        if (startBtn) startBtn.disabled = true;
        if (stopBtn) stopBtn.disabled = false;

        if (status.is_paused) {
            if (pauseBtn) pauseBtn.disabled = true;
            if (resumeBtn) resumeBtn.disabled = false;
            if (liveTag) {
                liveTag.textContent = "已暂停";
                liveTag.style.background = "var(--warning)";
                liveTag.style.color = "#000";
            }
        } else {
            if (pauseBtn) pauseBtn.disabled = false;
            if (resumeBtn) resumeBtn.disabled = true;
            if (liveTag) {
                liveTag.textContent = "正在抓取";
                liveTag.style.background = "var(--success)";
                liveTag.style.color = "#fff";
            }
        }
    } else {
        if (startBtn) startBtn.disabled = false;
        if (pauseBtn) pauseBtn.disabled = true;
        if (resumeBtn) resumeBtn.disabled = true;
        if (stopBtn) stopBtn.disabled = true;
        if (liveTag) {
            liveTag.textContent = "空闲就绪";
            liveTag.style.background = "var(--info)";
            liveTag.style.color = "#fff";
        }
    }

    if (statusMsg) statusMsg.textContent = status.status_message || "空闲";
    document.getElementById("m-current-cat").textContent = status.current_cat_name || "-";
    document.getElementById("m-current-page").textContent = status.current_page || 0;
    document.getElementById("m-total-crawled").textContent = (status.total_crawled_count || 0).toLocaleString();
    document.getElementById("m-new-crawled").textContent = (status.new_crawled_count || 0).toLocaleString();
    document.getElementById("m-elapsed-time").textContent = `${status.elapsed_seconds || 0}s`;

    // 进度条百分比估算
    let pct = 0;
    if (status.total_cats_count > 0) {
        pct = Math.round((status.current_cat_index / status.total_cats_count) * 100);
    }
    pct = Math.min(100, Math.max(0, pct));
    const fill = document.getElementById("crawl-progress-fill");
    const pctText = document.getElementById("crawl-percent-text");
    if (fill) fill.style.width = `${pct}%`;
    if (pctText) pctText.textContent = `${pct}%`;
}

async function loadCrawlProgressTable() {
    try {
        const res = await fetch("/api/crawler/progress_list");
        const data = await res.json();
        if (!data.success) return;

        const tbody = document.getElementById("tbody-cat-progress");
        if (!tbody) return;

        if (data.items.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" class="text-center">暂无子分类抓取进度归档</td></tr>`;
            return;
        }

        tbody.innerHTML = "";
        data.items.forEach(row => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><code>${row.category_code}</code></td>
                <td><strong>${escapeHtml(row.category_name)}</strong></td>
                <td>${(row.total_items || 0).toLocaleString()}</td>
                <td><span style="color:var(--success)">${(row.in_stock_items || 0).toLocaleString()}</span></td>
                <td><span class="badge-tag stock">${escapeHtml(row.status || 'DONE')}</span></td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        console.debug("获取分类进度表异常:", err);
    }
}

// --- SSE 实时广播流 ---
function initSSE() {
    const termBody = document.getElementById("terminal-output-container");
    const autoScrollCheck = document.getElementById("terminal-autoscroll");

    if (!window.EventSource) {
        console.warn("浏览器不支持 SSE，回退至定期轮询");
        return;
    }

    const evtSource = new EventSource("/api/events");

    evtSource.onmessage = (e) => {
        try {
            const payload = JSON.parse(e.data);
            if (payload.type === "log" && termBody) {
                const log = payload.data;
                appendTerminalLine(log);
            } else if (payload.type === "progress") {
                updateCrawlerUI(payload.data);
            } else if (payload.type === "presets" && Array.isArray(payload.data)) {
                handleRemotePresetUpdate(payload.data);
            } else if (payload.type === "monitor_records_updated") {
                handleNewMonitorRecordsEvent(payload);
            } else if (payload.type === "monitor_records_cleared") {
                handleRecordsClearedEvent();
            } else if (payload.type === "scheduler_status") {
                updateSchedulerUI(payload.data);
            } else if (payload.type === "purchase_status") {
                updatePurchaseStatusUI(payload.data);
            } else if (payload.type === "order_updated") {
                handleOrderUpdatedEvent(payload.order);
            } else if (payload.type === "orders_cleared") {
                handleOrdersClearedEvent();
            }
        } catch (err) {
            // ignore heartbeat
        }
    };

    evtSource.onerror = (err) => {
        console.debug("SSE 连接中断，正在自动重连...", err);
    };
}

const LOG_LEVEL_WEIGHTS = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "WARN": 30,
    "ERROR": 40,
    "CRITICAL": 50
};

function shouldDisplayLog(log) {
    if (!log) return false;
    const filterSelect = document.getElementById("terminal-level-select");
    const searchInput = document.getElementById("terminal-search");

    const curLevel = filterSelect ? filterSelect.value : "INFO";
    if (curLevel && curLevel !== "ALL") {
        const minWeight = LOG_LEVEL_WEIGHTS[curLevel.toUpperCase()] || 20;
        const logWeight = LOG_LEVEL_WEIGHTS[(log.level || "INFO").toUpperCase()] || 20;
        if (logWeight < minWeight) {
            return false;
        }
    }

    const keyword = searchInput ? searchInput.value.trim().toLowerCase() : "";
    if (keyword) {
        const text = `${log.formatted || ""} ${log.message || ""} ${log.logger || ""}`.toLowerCase();
        if (!text.includes(keyword)) {
            return false;
        }
    }
    return true;
}

function createLogLineElement(log) {
    const line = document.createElement("div");
    const lvl = (log.level || "info").toLowerCase();
    line.className = `log-line ${lvl === "warn" ? "warning" : lvl}`;
    line.textContent = log.formatted || `[${log.time_short || ""}] [${log.level || "INFO"}] ${log.message || ""}`;
    return line;
}

function renderTerminalLogs() {
    const termBody = document.getElementById("terminal-output-container");
    const autoScrollCheck = document.getElementById("terminal-autoscroll");
    if (!termBody) return;

    termBody.innerHTML = "";

    const logs = state.terminalLogs || [];
    const matchedLogs = logs.filter(shouldDisplayLog);

    if (matchedLogs.length === 0) {
        const emptyDiv = document.createElement("div");
        emptyDiv.className = "log-line debug";
        emptyDiv.textContent = "[SYS] 暂无符合当前过滤条件的日志输出...";
        termBody.appendChild(emptyDiv);
    } else {
        const maxDisplay = 500;
        const sliceLogs = matchedLogs.slice(-maxDisplay);
        const frag = document.createDocumentFragment();
        sliceLogs.forEach(log => {
            frag.appendChild(createLogLineElement(log));
        });
        termBody.appendChild(frag);
    }

    if (autoScrollCheck && autoScrollCheck.checked) {
        termBody.scrollTop = termBody.scrollHeight;
    }
}

async function loadTerminalLogs() {
    try {
        const res = await fetch("/api/terminal/logs?limit=500&level=ALL");
        const data = await res.json();
        if (data && data.success && Array.isArray(data.logs)) {
            state.terminalLogs = data.logs;
            if (data.logs.length > 0) {
                state.lastLogId = data.logs[data.logs.length - 1].id || 0;
            }
            renderTerminalLogs();
        }
    } catch (err) {
        console.debug("获取后台运行终端历史日志失败:", err);
    }
}

function appendTerminalLine(log) {
    if (!log) return;
    if (!state.terminalLogs) state.terminalLogs = [];

    // 去重避免重复渲染
    if (log.id && state.terminalLogs.some(existing => existing.id === log.id)) {
        return;
    }

    state.terminalLogs.push(log);
    if (log.id) state.lastLogId = log.id;
    if (state.terminalLogs.length > 1000) {
        state.terminalLogs.shift();
    }

    const termBody = document.getElementById("terminal-output-container");
    const autoScrollCheck = document.getElementById("terminal-autoscroll");
    if (!termBody) return;

    if (shouldDisplayLog(log)) {
        // 清除“暂无符合条件”占位行
        const placeholder = termBody.querySelector(".log-line.debug");
        if (placeholder && placeholder.textContent.includes("暂无符合当前过滤条件")) {
            placeholder.remove();
        }

        termBody.appendChild(createLogLineElement(log));

        if (termBody.children.length > 500) {
            termBody.removeChild(termBody.firstElementChild);
        }

        if (autoScrollCheck && autoScrollCheck.checked) {
            termBody.scrollTop = termBody.scrollHeight;
        }
    }
}

// 辅助工具：HTML 转义
function escapeHtml(text) {
    if (!text) return "";
    return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// --- 上新监控与提醒配置管理 ---
async function loadMonitorConfig() {
    try {
        const res = await fetch("/api/config");
        const data = await res.json();
        if (!data.success) return;

        const cfg = data.config || {};
        const sched = cfg.schedule || {};
        const notifiers = cfg.notifiers || {};
        const email = notifiers.email || {};
        const discord = notifiers.discord || {};

        // 1. 调度与重试参数
        const intervalInput = document.getElementById("cfg-interval-seconds");
        if (intervalInput) intervalInput.value = sched.interval_seconds !== undefined ? sched.interval_seconds : 15;
        const retryCountInput = document.getElementById("cfg-retry-count");
        if (retryCountInput) retryCountInput.value = sched.retry_count !== undefined ? sched.retry_count : 3;
        const retryDelayInput = document.getElementById("cfg-retry-delay");
        if (retryDelayInput) retryDelayInput.value = sched.retry_delay !== undefined ? sched.retry_delay : 2;
        const burstDurationInput = document.getElementById("cfg-burst-duration");
        if (burstDurationInput) burstDurationInput.value = sched.burst_duration_minutes !== undefined ? sched.burst_duration_minutes : 3;
        const burstIntervalInput = document.getElementById("cfg-burst-interval");
        if (burstIntervalInput) burstIntervalInput.value = sched.burst_interval_seconds !== undefined ? sched.burst_interval_seconds : 3;
        const burstRetryDelayInput = document.getElementById("cfg-burst-retry-delay");
        if (burstRetryDelayInput) burstRetryDelayInput.value = sched.burst_retry_delay !== undefined ? sched.burst_retry_delay : 1;
        const bypassCacheCheck = document.getElementById("cfg-bypass-cache");
        if (bypassCacheCheck) bypassCacheCheck.checked = sched.bypass_cache !== false;
        const autoBurstCheck = document.getElementById("cfg-auto-burst-on-hit");
        if (autoBurstCheck) autoBurstCheck.checked = sched.auto_burst_on_hit !== false;
        const monitorDispSelect = document.getElementById("cfg-monitor-disp-count");
        if (monitorDispSelect) monitorDispSelect.value = String(sched.monitor_disp_count || 48);

        // 2. 邮箱设置
        const emailCheck = document.getElementById("cfg-email-enabled");
        if (emailCheck) emailCheck.checked = Boolean(email.enabled);
        const emailHost = document.getElementById("cfg-email-host");
        if (emailHost) emailHost.value = email.smtp_host || "smtp.qq.com";
        const emailPort = document.getElementById("cfg-email-port");
        if (emailPort) emailPort.value = email.smtp_port || 465;
        const emailSsl = document.getElementById("cfg-email-ssl");
        if (emailSsl) emailSsl.checked = email.use_ssl !== false;
        const emailUser = document.getElementById("cfg-email-user");
        if (emailUser) emailUser.value = email.smtp_user || "";
        const emailPwd = document.getElementById("cfg-email-password");
        if (emailPwd) emailPwd.value = email.smtp_password || "";
        const emailReceiver = document.getElementById("cfg-email-receiver");
        if (emailReceiver) emailReceiver.value = email.receiver_email || "";

        // 3. Discord
        const discordCheck = document.getElementById("cfg-discord-enabled");
        if (discordCheck) discordCheck.checked = Boolean(discord.enabled);
        const discordWebhook = document.getElementById("cfg-discord-webhook");
        if (discordWebhook) discordWebhook.value = discord.webhook_url || "";

        // 5. 规则列表
        renderRulesTable(cfg.rules || []);

    } catch (err) {
        console.error("加载监控配置失败:", err);
    }
}

function splitKeywordsInput(str) {
    if (!str) return [];
    const tokens = String(str).split(/[\s,，、]+/).map(s => s.trim()).filter(Boolean);
    return Array.from(new Set(tokens));
}

function renderRulesTable(rules) {
    const tbody = document.getElementById("tbody-rules");
    if (!tbody) return;

    if (!rules || rules.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center" style="padding:40px;color:var(--text-muted)">暂无监控规则，请点击右上角【添加监控规则】进行创建</td></tr>`;
        return;
    }

    tbody.innerHTML = "";
    rules.forEach((rule, idx) => {
        const tr = document.createElement("tr");

        const normKeywords = splitKeywordsInput(Array.isArray(rule.keywords) ? rule.keywords.join(" ") : rule.keywords);
        const normExclude = splitKeywordsInput(Array.isArray(rule.exclude_keywords) ? rule.exclude_keywords.join(" ") : rule.exclude_keywords);

        // 关键词渲染 (含 AND / OR 模式标识)
        let kwHtml = "-";
        if (normKeywords.length > 0) {
            const mode = (rule.match_mode || "and").toUpperCase();
            kwHtml = `<span class="badge-mode">${mode}</span>` + 
                normKeywords.map(k => `<span class="kw-pill">${escapeHtml(k)}</span>`).join("");
        }

        // 排除词渲染
        let exHtml = "-";
        if (normExclude.length > 0) {
            exHtml = normExclude.map(e => `<span class="ex-kw-pill">-${escapeHtml(e)}</span>`).join("");
        }

        const maxPriceStr = rule.max_price > 0 ? `≤ ${rule.max_price.toLocaleString()}円` : "不限";
        const ruleId = rule.id || `rule_${idx}`;
        const ruleName = rule.name || "";
        const isEnabled = rule.enabled !== false;
        const isAutoBuy = Boolean(rule.auto_buy);

        tr.innerHTML = `
            <td><strong>${escapeHtml(ruleName)}</strong></td>
            <td><code>${escapeHtml(rule.category_code || '020114')}</code></td>
            <td>${kwHtml}</td>
            <td>${exHtml}</td>
            <td><strong style="color:var(--warning)">${maxPriceStr}</strong></td>
            <td>
                <label class="switch-toggle" title="开启后，在【抢购设置】开启总开关时命中此规则将自动下单">
                    <input type="checkbox" class="rule-autobuy-toggle" ${isAutoBuy ? 'checked' : ''}>
                    <span class="slider"></span>
                </label>
            </td>
            <td>
                <label class="switch-toggle" title="启用/暂停此规则">
                    <input type="checkbox" class="rule-status-toggle" ${isEnabled ? 'checked' : ''}>
                    <span class="slider"></span>
                </label>
            </td>
            <td>
                <div class="rule-actions">
                    <button class="btn btn-sm btn-outline-primary btn-edit-rule" title="编辑规则">
                        <i class="fa-solid fa-pen-to-square"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger btn-delete-rule" title="删除规则">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                </div>
            </td>
        `;

        const autoBuyToggle = tr.querySelector(".rule-autobuy-toggle");
        if (autoBuyToggle) {
            autoBuyToggle.addEventListener("change", () => {
                toggleRuleAutoBuy(ruleId, ruleName, autoBuyToggle.checked);
            });
        }

        const statusToggle = tr.querySelector(".rule-status-toggle");
        if (statusToggle) {
            statusToggle.addEventListener("change", () => {
                toggleRuleStatus(ruleId, ruleName, statusToggle.checked);
            });
        }

        const editBtn = tr.querySelector(".btn-edit-rule");
        if (editBtn) {
            editBtn.addEventListener("click", () => {
                openRuleModal({ ...rule, id: ruleId });
            });
        }

        const delBtn = tr.querySelector(".btn-delete-rule");
        if (delBtn) {
            delBtn.addEventListener("click", () => {
                deleteMonitorRule(ruleId, ruleName);
            });
        }

        tbody.appendChild(tr);
    });
}

function openRuleModal(rule = null) {
    const modal = document.getElementById("rule-modal");
    if (!modal) return;

    const isEdit = Boolean(rule);
    const titleEl = document.getElementById("rule-modal-title");
    if (titleEl) {
        titleEl.innerHTML = isEdit
            ? `<i class="fa-solid fa-pen-to-square"></i> 编辑关键词监控规则`
            : `<i class="fa-solid fa-plus-circle"></i> 添加关键词监控规则`;
    }
    const saveBtnEl = document.getElementById("btn-save-rule-modal");
    if (saveBtnEl) {
        saveBtnEl.innerHTML = isEdit
            ? `<i class="fa-solid fa-check"></i> 保存修改`
            : `<i class="fa-solid fa-check"></i> 保存并启用规则`;
    }

    const idInput = document.getElementById("modal-rule-id");
    if (idInput) {
        idInput.value = isEdit ? (rule.id || "") : "";
        idInput.dataset.enabled = isEdit ? String(rule.enabled !== false) : "true";
    }

    document.getElementById("modal-rule-name").value = isEdit ? (rule.name || "") : "";

    const catSelect = document.getElementById("modal-rule-cat");
    if (catSelect) {
        const targetCat = isEdit ? (rule.category_code || "020114") : "020114";
        const hasOption = Array.from(catSelect.options).some(opt => opt.value === targetCat);
        catSelect.value = hasOption ? targetCat : "020114";
    }

    const kwDisplay = isEdit && rule.keywords
        ? splitKeywordsInput(Array.isArray(rule.keywords) ? rule.keywords.join(" ") : rule.keywords).join(" ")
        : "";
    const exDisplay = isEdit && rule.exclude_keywords
        ? splitKeywordsInput(Array.isArray(rule.exclude_keywords) ? rule.exclude_keywords.join(" ") : rule.exclude_keywords).join(" ")
        : (isEdit ? "" : "難あり ジャンク");

    document.getElementById("modal-rule-keywords").value = kwDisplay;
    document.getElementById("modal-rule-exclude").value = exDisplay;
    document.getElementById("modal-rule-max-price").value = isEdit && rule.max_price ? rule.max_price : "";
    const autoBuyEl = document.getElementById("modal-rule-auto-buy");
    if (autoBuyEl) autoBuyEl.checked = isEdit ? Boolean(rule.auto_buy) : false;

    // 模式切换
    const mode = (isEdit ? (rule.match_mode || "and") : "and").toLowerCase();
    document.querySelectorAll("#modal-rule-mode-group .seg-btn").forEach(btn => {
        btn.classList.toggle("active", btn.getAttribute("data-mode") === mode);
    });

    modal.classList.add("active");
}

async function deleteMonitorRule(ruleId, ruleName = "") {
    const displayName = ruleName || ruleId || "此条监控规则";
    if (!confirm(`确定删除规则【${displayName}】吗？`)) return;
    try {
        const res = await fetch("/api/monitor/rules/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: ruleId, name: ruleName })
        });
        const data = await res.json();
        if (data.success) {
            renderRulesTable(data.rules || []);
        } else {
            alert("删除规则失败: " + (data.message || "未知错误"));
        }
    } catch (err) {
        alert("删除规则失败: " + err);
    }
}

async function toggleRuleAutoBuy(ruleId, ruleName, autoBuy) {
    try {
        const res = await fetch("/api/monitor/rules/toggle_auto_buy", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: ruleId, name: ruleName, auto_buy: autoBuy })
        });
        const data = await res.json();
        if (!data.success) {
            showToast(data.message || "更新规则自动抢购开关失败", "error");
            loadMonitorConfig();
        } else {
            showToast(`规则【${ruleName || ruleId}】自动抢购已${autoBuy ? "开启" : "关闭"}`, autoBuy ? "success" : "info");
        }
    } catch (err) {
        console.error("更新规则自动抢购状态异常:", err);
        loadMonitorConfig();
    }
}

async function toggleRuleStatus(ruleId, ruleName, enabled) {
    try {
        const res = await fetch("/api/monitor/rules/toggle", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: ruleId, name: ruleName, enabled: enabled })
        });
        const data = await res.json();
        if (!data.success) {
            console.error("更新规则状态失败:", data.message);
            loadMonitorConfig();
        }
    } catch (err) {
        console.error("更新规则状态异常:", err);
        loadMonitorConfig();
    }
}

function updateSchedulerUI(sched) {
    if (!sched) return;
    const dot = document.getElementById("sched-status-dot");
    const title = document.getElementById("sched-status-title");
    const meta = document.getElementById("sched-status-meta");
    const syncStatusEl = document.getElementById("daily-sync-status-text");

    if (syncStatusEl) {
        if (sched.is_syncing_daily) {
            syncStatusEl.innerHTML = `<span style="color: var(--warning);"><i class="fa-solid fa-spinner fa-spin"></i> 正在深度巡检细分分类最新库存与售罄状态...</span>`;
        } else if (sched.last_daily_sync_summary && sched.last_daily_sync_summary !== "尚未执行") {
            syncStatusEl.innerHTML = `<span style="color: var(--success);"><i class="fa-solid fa-check"></i> 上次巡检: ${escapeHtml(sched.last_daily_sync_summary)}</span>`;
        }
    }

    const burstBtn = document.getElementById("btn-monitor-burst");
    const pauseBtn = document.getElementById("btn-monitor-pause");

    if (sched.running) {
        if (sched.paused) {
            if (dot) dot.className = "status-indicator-dot paused";
            if (title) {
                title.textContent = "监控调度器已暂停 (待命中)";
            }
            if (pauseBtn) {
                pauseBtn.innerHTML = `<i class="fa-solid fa-play"></i> 恢复监控`;
                pauseBtn.className = "btn btn-success btn-sm";
                pauseBtn.title = "恢复后台自动监控巡检";
                pauseBtn.disabled = false;
            }
        } else {
            if (dot) dot.className = "status-indicator-dot";
            if (title) {
                if (sched.in_backoff) {
                    title.textContent = `🛡️ 触发官网限流，自动退避中 (剩余 ${sched.backoff_remaining_seconds || 0}s)`;
                } else if (sched.in_burst_mode) {
                    const burstLabel = sched.burst_reason === "auto" ? "⚡ 自动追击高频模式运行中" : "⚡ 高频爆发模式运行中";
                    title.textContent = `${burstLabel} (每 ${sched.burst_interval_seconds !== undefined ? sched.burst_interval_seconds : 3}s 扫描 | 剩余 ${sched.burst_remaining_seconds}s)`;
                } else {
                    title.textContent = `监控调度器运行中 (每 ${sched.interval_seconds !== undefined ? sched.interval_seconds : 15} 秒巡检一次)`;
                }
            }
            if (pauseBtn) {
                pauseBtn.innerHTML = `<i class="fa-solid fa-pause"></i> 暂停监控`;
                pauseBtn.className = "btn btn-secondary btn-sm";
                pauseBtn.title = "暂停后台自动监控巡检";
                pauseBtn.disabled = false;
            }
        }

        if (burstBtn) {
            if (sched.in_burst_mode) {
                burstBtn.innerHTML = `<i class="fa-solid fa-stop"></i> 停止高频爆发 (${sched.burst_remaining_seconds}s)`;
                burstBtn.className = "btn btn-danger btn-sm";
            } else {
                burstBtn.innerHTML = `<i class="fa-solid fa-bolt"></i> 启动高频爆发`;
                burstBtn.className = "btn btn-warning btn-sm";
            }
        }
    } else {
        if (dot) dot.className = "status-indicator-dot stopped";
        if (title) title.textContent = "监控调度器已停止";
        if (pauseBtn) {
            pauseBtn.innerHTML = `<i class="fa-solid fa-play"></i> 恢复监控`;
            pauseBtn.className = "btn btn-secondary btn-sm";
            pauseBtn.disabled = true;
        }
        if (burstBtn) {
            burstBtn.innerHTML = `<i class="fa-solid fa-bolt"></i> 启动高频爆发`;
            burstBtn.className = "btn btn-warning btn-sm";
        }
    }

    if (meta) {
        const lastCheckStr = sched.last_check_time > 0 
            ? new Date(sched.last_check_time * 1000).toLocaleTimeString("zh-CN", { hour12: false })
            : "从未";
        const scanMsStr = sched.last_scan_ms > 0 ? ` | 耗时: ${sched.last_scan_ms}ms` : "";
        const cacheStr = ` | 缓存穿透: ${sched.bypass_cache !== false ? "已开启" : "关闭"}`;
        const rlStr = sched.total_rate_limits > 0 ? ` | 限流次数: ${sched.total_rate_limits}` : "";
        meta.textContent = `上次检测: ${lastCheckStr} | 累计轮次: ${sched.total_checks || 0} | 累计命中: ${sched.total_matches || 0}${scanMsStr}${cacheStr}${rlStr}`;
    }
}

// --- 常用预设关键词管理与云端多端同步 ---

function updatePresetSyncStatus(status) {
    const el = document.getElementById("preset-sync-status");
    if (!el) return;
    el.classList.remove("syncing", "synced", "offline");
    if (status === "syncing") {
        el.classList.add("syncing");
        el.innerHTML = '<i class="fa-solid fa-cloud-arrow-up fa-fade"></i> <span class="sync-text">同步中</span>';
        el.title = "正在与服务端同步预设快捷标签...";
    } else if (status === "synced") {
        el.classList.add("synced");
        el.innerHTML = '<i class="fa-solid fa-cloud"></i> <span class="sync-text">已同步</span>';
        el.title = "已与服务端保持多端同步，点击可重新强制推送";
    } else if (status === "offline" || status === "error") {
        el.classList.add("offline");
        el.innerHTML = '<i class="fa-solid fa-cloud-slash"></i> <span class="sync-text">离线</span>';
        el.title = "同步失败，当前正在使用设备本地缓存";
    }
}

async function savePresetKeywordsToServer(keywords) {
    updatePresetSyncStatus("syncing");
    try {
        const res = await fetch("/api/presets", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ keywords: keywords || [] })
        }).then(r => r.json());
        if (res && res.success) {
            updatePresetSyncStatus("synced");
            return true;
        } else {
            updatePresetSyncStatus("offline");
            return false;
        }
    } catch (e) {
        console.warn("同步预设标签至服务端异常:", e);
        updatePresetSyncStatus("offline");
        return false;
    }
}

function handleRemotePresetUpdate(keywords) {
    if (!Array.isArray(keywords)) return;
    state.presetKeywords = keywords;
    try {
        localStorage.setItem(PRESET_STORAGE_KEY, JSON.stringify(keywords));
    } catch (_) {}
    state.selectedPresets = state.selectedPresets.filter(k => state.presetKeywords.includes(k));
    renderPresetKeywords();
    updatePresetSyncStatus("synced");
}

async function initPresetKeywords() {
    let localSaved = null;
    try {
        const saved = localStorage.getItem(PRESET_STORAGE_KEY);
        if (saved) {
            const parsed = JSON.parse(saved);
            if (Array.isArray(parsed) && parsed.length > 0) {
                localSaved = parsed;
                state.presetKeywords = parsed;
            } else {
                state.presetKeywords = [...DEFAULT_PRESET_KEYWORDS];
            }
        } else {
            state.presetKeywords = [...DEFAULT_PRESET_KEYWORDS];
        }
    } catch (e) {
        state.presetKeywords = [...DEFAULT_PRESET_KEYWORDS];
    }

    state.selectedPresets = [];
    state.presetMode = "or"; // 默认 OR 模式

    renderPresetKeywords();
    bindPresetEvents();

    // 从服务端拉取云端预设进行多端同步
    try {
        updatePresetSyncStatus("syncing");
        const res = await fetch("/api/presets").then(r => r.json());
        if (res && res.success) {
            if (res.is_custom && Array.isArray(res.keywords)) {
                // 服务端已有全局自定义预设配置，以服务端为主
                state.presetKeywords = res.keywords;
                try {
                    localStorage.setItem(PRESET_STORAGE_KEY, JSON.stringify(res.keywords));
                } catch (_) {}
                state.selectedPresets = state.selectedPresets.filter(k => state.presetKeywords.includes(k));
                renderPresetKeywords();
                updatePresetSyncStatus("synced");
            } else if (!res.is_custom) {
                // 服务端尚未保存过自定义预设：
                // 如果当前客户端（如电脑端）本地已有自定义修改（与内置默认不同），自动同步上传到服务端成为云端预设
                const isDifferentFromDefault = localSaved && (
                    localSaved.length !== DEFAULT_PRESET_KEYWORDS.length ||
                    localSaved.some((kw, i) => kw !== DEFAULT_PRESET_KEYWORDS[i])
                );
                if (isDifferentFromDefault) {
                    await savePresetKeywordsToServer(localSaved);
                } else {
                    updatePresetSyncStatus("synced");
                }
            }
        } else {
            updatePresetSyncStatus("offline");
        }
    } catch (e) {
        console.warn("拉取服务端预设标签异常，继续使用本地预设:", e);
        updatePresetSyncStatus("offline");
    }
}

function renderPresetKeywords() {
    const listEl = document.getElementById("preset-tags-list");
    if (!listEl) return;

    listEl.innerHTML = "";

    state.presetKeywords.forEach(kw => {
        const isActive = state.selectedPresets.includes(kw);
        const tag = document.createElement("div");
        tag.className = `preset-tag ${isActive ? 'active' : ''}`;
        tag.setAttribute("data-kw", kw);

        tag.innerHTML = `
            <span class="tag-text">${escapeHtml(kw)}</span>
            <span class="tag-del" title="删除预设标签: ${escapeHtml(kw)}" data-del-kw="${escapeHtml(kw)}">
                <i class="fa-solid fa-xmark"></i>
            </span>
        `;

        // 点击标签切换选中
        tag.addEventListener("click", (e) => {
            if (e.target.closest(".tag-del")) return;
            togglePresetKeyword(kw);
        });

        // 点击删除按钮删除
        const delBtn = tag.querySelector(".tag-del");
        if (delBtn) {
            delBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                deletePresetKeyword(kw);
            });
        }

        listEl.appendChild(tag);
    });

    // 模式切换高亮
    const andBtn = document.getElementById("preset-mode-and");
    const orBtn = document.getElementById("preset-mode-or");
    if (andBtn && orBtn) {
        andBtn.classList.toggle("active", state.presetMode === "and");
        orBtn.classList.toggle("active", state.presetMode === "or");
    }

    // “清除选中”按纽显隐
    const clearSelectedBtn = document.getElementById("btn-clear-selected-presets");
    if (clearSelectedBtn) {
        clearSelectedBtn.style.display = state.selectedPresets.length > 0 ? "inline-flex" : "none";
    }
}

function togglePresetKeyword(kw) {
    const idx = state.selectedPresets.indexOf(kw);
    if (idx > -1) {
        state.selectedPresets.splice(idx, 1);
    } else {
        state.selectedPresets.push(kw);
    }
    renderPresetKeywords();
    state.currentPage = 1;
    loadProducts();
}

function deletePresetKeyword(kw) {
    if (!confirm(`确定要移除预设标签「${kw}」吗？`)) return;
    state.presetKeywords = state.presetKeywords.filter(k => k !== kw);
    state.selectedPresets = state.selectedPresets.filter(k => k !== kw);
    savePresetKeywords();
    renderPresetKeywords();
    state.currentPage = 1;
    loadProducts();
}

function savePresetKeywords() {
    try {
        localStorage.setItem(PRESET_STORAGE_KEY, JSON.stringify(state.presetKeywords));
    } catch (e) {
        console.error("保存预设标签异常:", e);
    }
    // 同步到服务端（跨设备多端同步）
    savePresetKeywordsToServer(state.presetKeywords);
}

function addPresetKeyword(newKw) {
    const kw = (newKw || "").trim();
    if (!kw) return;
    if (state.presetKeywords.includes(kw)) {
        alert(`预设标签「${kw}」已存在！`);
        return;
    }
    state.presetKeywords.push(kw);
    savePresetKeywords();
    renderPresetKeywords();
}

function bindPresetEvents() {
    // AND 模式
    document.getElementById("preset-mode-and")?.addEventListener("click", () => {
        if (state.presetMode === "and") return;
        state.presetMode = "and";
        renderPresetKeywords();
        if (state.selectedPresets.length > 0) {
            state.currentPage = 1;
            loadProducts();
        }
    });

    // OR 模式
    document.getElementById("preset-mode-or")?.addEventListener("click", () => {
        if (state.presetMode === "or") return;
        state.presetMode = "or";
        renderPresetKeywords();
        if (state.selectedPresets.length > 0) {
            state.currentPage = 1;
            loadProducts();
        }
    });

    // 添加标签交互：内联展开
    const addBtn = document.getElementById("btn-add-preset");
    const inlineBox = document.getElementById("preset-add-inline");
    const inputNew = document.getElementById("input-new-preset");
    const confirmBtn = document.getElementById("btn-confirm-add-preset");
    const cancelBtn = document.getElementById("btn-cancel-add-preset");

    addBtn?.addEventListener("click", () => {
        if (!inlineBox || !addBtn) return;
        inlineBox.style.display = "inline-flex";
        addBtn.style.display = "none";
        if (inputNew) {
            inputNew.value = "";
            inputNew.focus();
        }
    });

    const closeInlineAdd = () => {
        if (!inlineBox || !addBtn) return;
        inlineBox.style.display = "none";
        addBtn.style.display = "inline-flex";
        if (inputNew) inputNew.value = "";
    };

    cancelBtn?.addEventListener("click", closeInlineAdd);

    const doAdd = () => {
        if (!inputNew) return;
        const val = inputNew.value.trim();
        if (val) {
            addPresetKeyword(val);
            closeInlineAdd();
        } else {
            inputNew.focus();
        }
    };

    confirmBtn?.addEventListener("click", doAdd);
    inputNew?.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            doAdd();
        } else if (e.key === "Escape") {
            e.preventDefault();
            closeInlineAdd();
        }
    });

    // 清除选中
    document.getElementById("btn-clear-selected-presets")?.addEventListener("click", () => {
        state.selectedPresets = [];
        renderPresetKeywords();
        state.currentPage = 1;
        loadProducts();
    });

    // 点击同步状态图标手动强制同步
    document.getElementById("preset-sync-status")?.addEventListener("click", () => {
        savePresetKeywordsToServer(state.presetKeywords);
    });

    // 恢复默认预设
    document.getElementById("btn-reset-presets")?.addEventListener("click", () => {
        if (confirm("确定要恢复默认预设标签列表并同步到云端吗？")) {
            state.presetKeywords = [...DEFAULT_PRESET_KEYWORDS];
            savePresetKeywords();
            renderPresetKeywords();
        }
    });
}


// --- 移动端交互与触屏特性增强 ---
function initMobileInteractions() {
    // 1. 移动端筛选折叠面板切换
    const toggleBtn = document.getElementById("btn-toggle-filters");
    const collapsible = document.getElementById("filter-collapsible-wrap");
    if (toggleBtn && collapsible) {
        toggleBtn.addEventListener("click", () => {
            const isOpen = collapsible.classList.toggle("open");
            const toggleText = toggleBtn.querySelector(".toggle-text");
            if (toggleText) {
                toggleText.textContent = isOpen ? "收起筛选" : "更多筛选";
            }
            const icon = toggleBtn.querySelector("i");
            if (icon) {
                icon.className = isOpen ? "fa-solid fa-chevron-up" : "fa-solid fa-sliders";
            }
        });
    }

    // 2. 悬浮回到顶部按钮
    const bttBtn = document.getElementById("btn-back-to-top");
    if (bttBtn) {
        window.addEventListener("scroll", () => {
            if (window.scrollY > 280) {
                bttBtn.classList.add("visible");
            } else {
                bttBtn.classList.remove("visible");
            }
        }, { passive: true });

        bttBtn.addEventListener("click", () => {
            window.scrollTo({ top: 0, behavior: "smooth" });
        });
    }

    // 3. 页面滚动时关闭已打开但失焦的输入软键盘
    document.addEventListener("touchmove", () => {
        if (document.activeElement && (document.activeElement.tagName === "INPUT" || document.activeElement.tagName === "SELECT")) {
            // 用户在滑动浏览商品列表时若键盘开启，可适时收起
        }
    }, { passive: true });

    updateActiveFilterCount();
}

function updateActiveFilterCount() {
    const badge = document.getElementById("active-filter-count");
    if (!badge) return;

    let count = 0;
    if (state.categoryCode && state.categoryCode !== "020114") count++;
    if ((state.sortBy && state.sortBy !== "id") || (state.sortOrder && state.sortOrder !== "desc")) count++;
    if (state.excludeKeyword) count++;
    if (state.minPrice || state.maxPrice) count++;
    if (state.selectedPresets && state.selectedPresets.length > 0) count += state.selectedPresets.length;

    if (count > 0) {
        badge.textContent = count;
        badge.style.display = "inline-block";
    } else {
        badge.style.display = "none";
    }
}


// 注册 PWA Service Worker (满足移动端可安装性检测)
if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
        navigator.serviceWorker.register("/sw.js", { scope: "/" })
            .then(reg => {
                console.log("[PWA] Service Worker 注册成功, scope:", reg.scope);
            })
            .catch(err => {
                console.warn("[PWA] Service Worker 注册被跳过或失败:", err);
            });
    });
}

// ==========================================================================
// 上新记录前端模块 (Monitor Records Module)
// ==========================================================================

function initMonitorRecords() {
    // 1. 刷新记录按钮
    const btnRefresh = document.getElementById("btn-rec-refresh");
    btnRefresh?.addEventListener("click", () => {
        const icon = btnRefresh.querySelector("i");
        if (icon) icon.classList.add("fa-spin");
        loadMonitorRecords(state.records.page).finally(() => {
            setTimeout(() => {
                if (icon) icon.classList.remove("fa-spin");
            }, 400);
        });
        loadRecordsStats();
    });

    // 2. 关键词搜索
    const kwInput = document.getElementById("rec-keyword");
    kwInput?.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            state.records.keyword = kwInput.value.trim();
            loadMonitorRecords(1);
        }
    });

    // 3. 筛选按钮
    document.getElementById("btn-rec-filter")?.addEventListener("click", () => {
        if (kwInput) state.records.keyword = kwInput.value.trim();
        const ruleSelect = document.getElementById("rec-rule-select");
        if (ruleSelect) state.records.ruleName = ruleSelect.value;
        loadMonitorRecords(1);
    });

    // 4. 重置筛选
    document.getElementById("btn-rec-reset")?.addEventListener("click", () => {
        if (kwInput) kwInput.value = "";
        const ruleSelect = document.getElementById("rec-rule-select");
        if (ruleSelect) ruleSelect.value = "";
        state.records.keyword = "";
        state.records.ruleName = "";
        loadMonitorRecords(1);
    });

    // 5. 规则筛选下拉联动
    document.getElementById("rec-rule-select")?.addEventListener("change", (e) => {
        state.records.ruleName = e.target.value;
        loadMonitorRecords(1);
    });

    // 6. 每页显示数量变更联动
    document.getElementById("rec-page-size")?.addEventListener("change", (e) => {
        const sz = parseInt(e.target.value, 10);
        if (sz > 0) {
            state.records.pageSize = sz;
            loadMonitorRecords(1);
        }
    });

    // 7. 清空所有记录
    document.getElementById("btn-rec-clear")?.addEventListener("click", () => {
        clearMonitorRecords();
    });

    // 8. 导入示例记录 (便于初始体验)
    document.getElementById("btn-rec-mock")?.addEventListener("click", () => {
        mockMonitorRecords();
    });

    // 9. 记录列表事件委托 (终极保障：防止任何单卡片事件绑定失效)
    const listContainer = document.getElementById("monitor-records-list");
    listContainer?.addEventListener("click", (e) => {
        const delBtn = e.target.closest(".btn-del-record");
        if (delBtn) {
            e.stopPropagation();
            const card = delBtn.closest(".record-card");
            const recordId = card?.getAttribute("data-record-id");
            if (recordId) {
                deleteMonitorRecord(recordId, card);
            }
            return;
        }

        const copyBtn = e.target.closest(".btn-copy-url");
        if (copyBtn) {
            e.stopPropagation();
            const card = copyBtn.closest(".record-card");
            const itemUrl = card?.getAttribute("data-item-url");
            if (itemUrl) copyTextToClipboard(itemUrl);
            return;
        }

        const openBtn = e.target.closest(".btn-open-url");
        if (openBtn) {
            e.stopPropagation();
            const card = openBtn.closest(".record-card");
            const itemUrl = card?.getAttribute("data-item-url");
            if (itemUrl) window.open(itemUrl, "_blank", "noopener,noreferrer");
            return;
        }
    });

    // 初始读取统计信息
    loadRecordsStats();
}

/**
 * 加载上新记录统计与规则列表
 */
async function loadRecordsStats() {
    try {
        const resp = await fetch("/api/monitor/records/stats");
        const data = await resp.json();
        if (data.success && data.stats) {
            const stats = data.stats;
            const totalEl = document.getElementById("rec-total-count");
            const todayEl = document.getElementById("rec-today-count");
            if (totalEl) totalEl.textContent = stats.total || 0;
            if (todayEl) todayEl.textContent = stats.today_count || 0;

            // 填充规则下拉框
            const ruleSelect = document.getElementById("rec-rule-select");
            if (ruleSelect && Array.isArray(stats.rules)) {
                const currentVal = ruleSelect.value;
                let html = '<option value="">全部规则</option>';
                stats.rules.forEach(ruleName => {
                    const selected = ruleName === currentVal ? "selected" : "";
                    html += `<option value="${escapeHtml(ruleName)}" ${selected}>${escapeHtml(ruleName)}</option>`;
                });
                ruleSelect.innerHTML = html;
            }
        }
    } catch (err) {
        console.debug("获取上新记录统计失败:", err);
    }
}

/**
 * 加载分页上新记录
 */
async function loadMonitorRecords(page = 1) {
    const container = document.getElementById("monitor-records-list");
    if (!container) return;

    state.records.page = page;

    // 显示加载骨架状态
    container.innerHTML = `
        <div style="padding: 40px; text-align: center; color: var(--text-muted);">
            <i class="fa-solid fa-spinner fa-spin fa-2x"></i>
            <p style="margin-top: 10px; font-size: 13px;">正在加载上新提醒记录...</p>
        </div>
    `;

    try {
        const params = new URLSearchParams({
            page: page,
            page_size: state.records.pageSize,
            keyword: state.records.keyword,
            rule_name: state.records.ruleName
        });

        const resp = await fetch(`/api/monitor/records?${params.toString()}`);
        const data = await resp.json();

        if (!data.success) {
            container.innerHTML = `<div class="records-empty-box"><p>读取记录失败: ${escapeHtml(data.message || '未知错误')}</p></div>`;
            return;
        }

        state.records.totalItems = data.total;
        state.records.totalPages = data.total_pages;

        // 渲染记录与分页
        renderMonitorRecords(data.records);
        renderRecordsPagination(data.total, data.page, data.page_size, data.total_pages);

        // 同步更新统计
        const totalEl = document.getElementById("rec-total-count");
        if (totalEl) totalEl.textContent = data.total;

    } catch (err) {
        container.innerHTML = `
            <div class="records-empty-box">
                <i class="fa-solid fa-triangle-exclamation empty-icon" style="color: #f87171;"></i>
                <h3>加载上新提醒记录异常</h3>
                <p>${escapeHtml(String(err))}</p>
                <div class="actions">
                    <button class="btn btn-secondary btn-sm" onclick="loadMonitorRecords(${page})">重试加载</button>
                </div>
            </div>
        `;
    }
}

/**
 * 渲染记录列表
 */
function renderMonitorRecords(records) {
    const container = document.getElementById("monitor-records-list");
    if (!container) return;

    if (!records || records.length === 0) {
        container.innerHTML = `
            <div class="records-empty-box">
                <i class="fa-solid fa-inbox empty-icon"></i>
                <h3>暂无上新提醒记录</h3>
                <p>当后台监控巡检命中符合您关注规则的新上架商品时，会立即自动将商品图文、价格与详情永久记录在此处。</p>
                <div class="actions">
                    <button class="btn btn-primary btn-sm" onclick="checkMonitorNow()">
                        <i class="fa-solid fa-magnifying-glass"></i> 立即巡检一次
                    </button>
                    <button class="btn btn-secondary btn-sm" onclick="mockMonitorRecords()">
                        <i class="fa-solid fa-wand-magic-sparkles"></i> 导入示例记录体验
                    </button>
                </div>
            </div>
        `;
        return;
    }

    container.innerHTML = "";
    records.forEach(item => {
        const card = createMonitorRecordCard(item);
        container.appendChild(card);
    });
}

/**
 * 创建单条上新记录卡片 DOM
 */
function createMonitorRecordCard(item) {
    const card = document.createElement("div");
    card.className = "record-card";
    card.setAttribute("data-record-id", item.id);
    const itemUrl = item.url || `https://order.mandarake.co.jp/order/detailPage/item?itemCode=${item.item_id}`;
    card.setAttribute("data-item-url", itemUrl);

    // 缩略图 fallback 与商品详情链接
    const imgUrl = item.img_url || DEFAULT_ITEM_THUMB;
    const priceText = item.price ? `${item.price.toLocaleString()}円` : "价格未标明";
    const shopText = item.shop || "まんだらけ";
    const ruleText = item.rule_name || "默认监控规则";
    const catText = item.category_name || "ドール";
    const itemNoText = item.item_no || item.item_id;

    const fullTimeStr = formatTimestamp(item.created_at);
    const relTimeStr = formatRelativeTime(item.created_at);

    card.innerHTML = `
        <div class="record-thumb-wrap" title="点击新窗口打开商品图片或直达官网" onclick="window.open('${escapeHtml(itemUrl)}', '_blank', 'noopener,noreferrer')">
            <img src="${escapeHtml(imgUrl)}" alt="${escapeHtml(item.title)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.onerror=null;this.src='/static/icon-192.png'">
            <span class="record-thumb-tag">⚡ 上新命中</span>
        </div>
        <div class="record-details">
            <div class="record-badges-row">
                <span class="rec-badge rule" title="触发该提醒的监控规则"><i class="fa-solid fa-crosshairs"></i> ${escapeHtml(ruleText)}</span>
                <span class="rec-badge shop" title="所属店铺"><i class="fa-solid fa-store"></i> ${escapeHtml(shopText)}</span>
                <span class="rec-badge cat" title="分类路径: ${escapeHtml(catText)}"><i class="fa-solid fa-tag"></i> ${escapeHtml(catText)}</span>
                <span class="rec-badge item-no" title="商品编号"><i class="fa-solid fa-barcode"></i> ${escapeHtml(itemNoText)}</span>
            </div>
            <h3 class="record-title" title="${escapeHtml(item.title)}" onclick="window.open('${escapeHtml(itemUrl)}', '_blank', 'noopener,noreferrer')">
                ${escapeHtml(item.title)}
            </h3>
            <div class="record-bottom-row">
                <div class="record-price-wrap">
                    <span class="record-price">${priceText}</span>
                    <span class="record-price-str">${escapeHtml(item.price_str || '')}</span>
                </div>
                <div class="record-time-meta" title="提醒触发时间：${escapeHtml(fullTimeStr)}">
                    <i class="fa-regular fa-clock"></i>
                    <span>${escapeHtml(fullTimeStr)}</span>
                    <span class="rel-time">(${relTimeStr})</span>
                </div>
            </div>
        </div>
        <div class="record-actions">
            <button class="btn btn-warning btn-sm btn-rec-quick-buy" title="一键下单抢购该商品">
                <i class="fa-solid fa-bolt"></i> 一键抢购
            </button>
            <button class="btn btn-primary btn-sm btn-open-url" title="在新标签页中打开 Mandarake 官网详情页">
                <i class="fa-solid fa-arrow-up-right-from-square"></i> 官网直达
            </button>
            <button class="btn btn-secondary btn-sm btn-copy-url" title="复制 Mandarake 官网商品链接">
                <i class="fa-regular fa-copy"></i> 复制链接
            </button>
            <button class="btn btn-danger btn-sm btn-del-record" title="从历史提醒记录中删除该条目">
                <i class="fa-solid fa-trash-can"></i> 删除
            </button>
        </div>
    `;

    // 绑定卡片内按钮事件
    const recBuyBtn = card.querySelector(".btn-rec-quick-buy");
    recBuyBtn?.addEventListener("click", (e) => {
        e.stopPropagation();
        handleOneClickBuy({
            id: item.item_id,
            title: item.title,
            price: item.price || 0,
            img_url: item.img_url || "",
            shop: item.shop || "",
            url: itemUrl
        }, recBuyBtn);
    });

    card.querySelector(".btn-open-url")?.addEventListener("click", (e) => {
        e.stopPropagation();
        window.open(itemUrl, "_blank", "noopener,noreferrer");
    });

    card.querySelector(".btn-copy-url")?.addEventListener("click", (e) => {
        e.stopPropagation();
        copyTextToClipboard(itemUrl);
    });

    card.querySelector(".btn-del-record")?.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteMonitorRecord(item.id, card);
    });

    return card;
}

/**
 * 渲染上新记录分页控件
 */
function renderRecordsPagination(total, page, pageSize, totalPages) {
    const infoEl = document.getElementById("records-pagination-info");
    const controlsEl = document.getElementById("records-pagination-controls");
    if (!controlsEl) return;

    if (total === 0) {
        if (infoEl) infoEl.textContent = "暂无记录";
        controlsEl.innerHTML = "";
        return;
    }

    const start = (page - 1) * pageSize + 1;
    const end = Math.min(page * pageSize, total);
    if (infoEl) {
        infoEl.textContent = `显示第 ${start} - ${end} 条，共 ${total} 条记录 (第 ${page} / ${totalPages} 页)`;
    }

    if (totalPages <= 1) {
        controlsEl.innerHTML = "";
        return;
    }

    let html = `<button class="page-btn" data-rec-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>上一页</button>`;

    // 缩略分页算法
    let pages = [];
    if (totalPages <= 7) {
        for (let i = 1; i <= totalPages; i++) pages.push(i);
    } else {
        pages.push(1);
        if (page > 3) pages.push("...");
        for (let i = Math.max(2, page - 1); i <= Math.min(totalPages - 1, page + 1); i++) {
            pages.push(i);
        }
        if (page < totalPages - 2) pages.push("...");
        pages.push(totalPages);
    }

    pages.forEach(p => {
        if (p === "...") {
            html += `<span class="page-ellipsis" style="color:var(--text-muted);padding:0 4px;">...</span>`;
        } else {
            html += `<button class="page-btn ${p === page ? 'active' : ''}" data-rec-page="${p}">${p}</button>`;
        }
    });

    html += `<button class="page-btn" data-rec-page="${page + 1}" ${page >= totalPages ? 'disabled' : ''}>下一页</button>`;

    controlsEl.innerHTML = html;

    // 绑定分页点击事件
    controlsEl.querySelectorAll(".page-btn[data-rec-page]").forEach(btn => {
        btn.addEventListener("click", () => {
            const p = parseInt(btn.getAttribute("data-rec-page"), 10);
            if (p && p !== page && p >= 1 && p <= totalPages) {
                loadMonitorRecords(p);
                const wrap = document.getElementById("records-list-wrapper");
                if (wrap) {
                    wrap.scrollIntoView({ behavior: "smooth", block: "start" });
                }
            }
        });
    });
}

/**
 * 删除单条上新提醒记录
 */
async function deleteMonitorRecord(recordId, cardEl) {
    if (!recordId) return;

    if (cardEl && cardEl.getAttribute("data-deleting") === "true") return;
    if (cardEl) cardEl.setAttribute("data-deleting", "true");

    try {
        const resp = await fetch(`/api/monitor/records/${recordId}`, { 
            method: "DELETE",
            headers: { "X-Requested-With": "XMLHttpRequest" }
        });
        const data = await resp.json();
        if (data.success) {
            showToast("已删除该条上新记录", "info");
            if (cardEl) {
                cardEl.style.transition = "all 0.28s cubic-bezier(0.4, 0, 0.2, 1)";
                cardEl.style.opacity = "0";
                cardEl.style.transform = "translateX(35px)";
                setTimeout(() => {
                    cardEl.remove();
                    const remaining = document.querySelectorAll(".record-card").length;
                    if (remaining === 0) {
                        const targetPage = Math.max(1, state.records.page - 1);
                        loadMonitorRecords(targetPage);
                    } else {
                        loadMonitorRecords(state.records.page);
                    }
                    loadRecordsStats();
                }, 280);
            } else {
                loadMonitorRecords(state.records.page);
                loadRecordsStats();
            }
        } else {
            if (cardEl) cardEl.removeAttribute("data-deleting");
            showToast(data.message || "删除失败", "error");
        }
    } catch (err) {
        if (cardEl) cardEl.removeAttribute("data-deleting");
        showToast(`删除异常: ${err}`, "error");
    }
}

/**
 * 一键清空所有上新提醒记录
 */
async function clearMonitorRecords() {
    if (!confirm("⚠️ 确定要清空全部上新提醒记录吗？\n所有历史提醒条目将被移除，此操作不可撤销。")) {
        return;
    }

    try {
        const resp = await fetch("/api/monitor/records/clear", { method: "POST" });
        const data = await resp.json();
        if (data.success) {
            showToast("已清空所有上新提醒记录", "success");
            loadMonitorRecords(1);
            loadRecordsStats();
        } else {
            showToast(data.message || "清空失败", "error");
        }
    } catch (err) {
        showToast(`清空异常: ${err}`, "error");
    }
}

/**
 * 导入示例上新记录 (辅助体验)
 */
async function mockMonitorRecords() {
    try {
        const resp = await fetch("/api/monitor/records/mock_from_db", { method: "POST" });
        const data = await resp.json();
        if (data.success) {
            showToast(data.message || "示例记录导入成功！", "success");
            loadMonitorRecords(1);
            loadRecordsStats();
        } else {
            showToast(data.message || "导入失败", "warning");
        }
    } catch (err) {
        showToast(`操作异常: ${err}`, "error");
    }
}

/**
 * 响应 SSE 监控上新记录广播
 */
function handleNewMonitorRecordsEvent(payload) {
    const count = payload.count || 1;
    // 更新上新记录选项卡右上角未读 badge
    const badge = document.getElementById("monitor-records-badge");
    if (badge) {
        state.records.unreadCount = (state.records.unreadCount || 0) + count;
        badge.textContent = state.records.unreadCount;
        badge.style.display = "inline-flex";
    }

    // 若当前正在查看“上新记录”页面，自动静默刷新当前列表
    if (state.activeTab === "tab-monitor-records") {
        loadMonitorRecords(state.records.page);
        loadRecordsStats();
    } else {
        showToast(`🔔 监控巡检命中新商品！已存入 ${count} 条上新记录`, "success");
    }
}

/**
 * 响应 SSE 记录清空广播
 */
function handleRecordsClearedEvent() {
    if (state.activeTab === "tab-monitor-records") {
        loadMonitorRecords(1);
        loadRecordsStats();
    }
}

/**
 * 复制文本至剪贴板
 */
function copyTextToClipboard(text) {
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
            showToast("商品官网链接已复制到剪贴板！", "success");
        }).catch(() => {
            fallbackCopy(text);
        });
    } else {
        fallbackCopy(text);
    }
}

function fallbackCopy(text) {
    const input = document.createElement("input");
    input.value = text;
    document.body.appendChild(input);
    input.select();
    try {
        document.execCommand("copy");
        showToast("商品官网链接已复制到剪贴板！", "success");
    } catch (e) {
        prompt("请手动复制链接：", text);
    }
    document.body.removeChild(input);
}

/**
 * 格式化时间戳为 YYYY-MM-DD HH:mm:ss
 */
function formatTimestamp(ts) {
    if (!ts) return "未知时间";
    // 如果是秒级时间戳转换为毫秒
    const ms = ts < 10000000000 ? ts * 1000 : ts;
    const d = new Date(ms);
    const pad = (n) => String(n).padStart(2, "0");
    const y = d.getFullYear();
    const m = pad(d.getMonth() + 1);
    const date = pad(d.getDate());
    const h = pad(d.getHours());
    const min = pad(d.getMinutes());
    const s = pad(d.getSeconds());
    return `${y}-${m}-${date} ${h}:${min}:${s}`;
}

/**
 * 计算人性化相对时间 (刚刚 / X 分钟前 / X 小时前 / 昨天 / 日期)
 */
function formatRelativeTime(ts) {
    if (!ts) return "";
    const ms = ts < 10000000000 ? ts * 1000 : ts;
    const diffSec = Math.floor((Date.now() - ms) / 1000);

    if (diffSec < 60) {
        return "刚刚";
    } else if (diffSec < 3600) {
        return `${Math.floor(diffSec / 60)} 分钟前`;
    } else if (diffSec < 86400) {
        return `${Math.floor(diffSec / 3600)} 小时前`;
    } else if (diffSec < 172800) {
        return "昨天";
    } else {
        const days = Math.floor(diffSec / 86400);
        return days < 30 ? `${days} 天前` : `${Math.floor(days / 30)} 个月前`;
    }
}

// 暴露供 HTML 内联事件调用的全局辅助函数
function checkMonitorNow() {
    const btn = document.getElementById("btn-monitor-check-now");
    if (btn) {
        btn.click();
    } else {
        fetch("/api/monitor/check_now", { method: "POST" })
            .then(res => res.json())
            .then(data => {
                showToast(`巡检完成！命中 ${data.matches_count || 0} 件新商品`, "success");
                loadMonitorRecords(1);
                loadRecordsStats();
            });
    }
}

window.checkMonitorNow = checkMonitorNow;
window.mockMonitorRecords = mockMonitorRecords;
window.loadMonitorRecords = loadMonitorRecords;

/**
 * 全局浮层 Toast 提示组件
 * @param {string} message 提示文本
 * @param {'info'|'success'|'warning'|'error'} type 提示类型
 * @param {number} duration 显示时长 (ms)
 */
function showToast(message, type = "info", duration = 3000) {
    if (!message) return;
    try {
        let container = document.getElementById("global-toast-container");
        if (!container) {
            container = document.createElement("div");
            container.id = "global-toast-container";
            container.className = "toast-container";
            document.body.appendChild(container);
        }

        const toast = document.createElement("div");
        toast.className = `toast-item ${type}`;

        const iconMap = {
            success: "fa-solid fa-circle-check",
            error: "fa-solid fa-circle-xmark",
            warning: "fa-solid fa-triangle-exclamation",
            info: "fa-solid fa-circle-info"
        };
        const iconClass = iconMap[type] || iconMap.info;

        toast.innerHTML = `
            <i class="toast-icon ${iconClass}"></i>
            <span class="toast-msg">${escapeHtml(String(message))}</span>
            <button class="toast-close" title="关闭"><i class="fa-solid fa-xmark"></i></button>
        `;

        container.appendChild(toast);

        requestAnimationFrame(() => {
            toast.classList.add("show");
        });

        let timer = null;
        const removeToast = () => {
            if (timer) clearTimeout(timer);
            toast.classList.remove("show");
            toast.classList.add("hide");
            setTimeout(() => {
                toast.remove();
                if (container.children.length === 0) {
                    container.remove();
                }
            }, 300);
        };

        toast.querySelector(".toast-close")?.addEventListener("click", removeToast);
        if (duration > 0) {
            timer = setTimeout(removeToast, duration);
        }
    } catch (e) {
        console.log(`[Toast ${type}]`, message);
    }
}

window.showToast = showToast;

// ==========================================================================
// 抢购设置 & 一键下单模块 (Purchase & Auto-Order Module)
// ==========================================================================

function initPurchaseModule() {
    // 顶部快捷 Dry-Run 开关
    document.getElementById("p-top-dry-run-toggle")?.addEventListener("change", async (e) => {
        const dryRun = Boolean(e.target.checked);
        if (!dryRun && !confirm("⚠️ 确定关闭 Dry-Run 仿真演练、切换为【实盘下单模式】吗？\n切换后一键抢购与自动抢购将会向 Mandarake 提交真实订单！")) {
            e.target.checked = true;
            return;
        }
        await savePurchaseConfigPartial({ dry_run: dryRun }, dryRun ? "已开启 Dry-Run 仿真演练保护" : "⚠️ 已切换为 LIVE 实盘抢购模式");
    });

    // 顶部快捷自动抢购开关
    document.getElementById("p-top-auto-buy-toggle")?.addEventListener("change", async (e) => {
        const autoEnabled = Boolean(e.target.checked);
        await savePurchaseConfigPartial({ auto_buy_enabled: autoEnabled }, autoEnabled ? "⚡ 已开启自动抢购总开关" : "已关闭自动抢购");
    });

    // 卡片内部开关同步
    document.getElementById("p-dry-run")?.addEventListener("change", (e) => {
        const topDry = document.getElementById("p-top-dry-run-toggle");
        if (topDry) topDry.checked = e.target.checked;
        updatePurchaseModeDecorators(e.target.checked);
    });

    document.getElementById("p-auto-buy-enabled")?.addEventListener("change", (e) => {
        const topAuto = document.getElementById("p-top-auto-buy-toggle");
        if (topAuto) topAuto.checked = e.target.checked;
    });

    // 自定义地址开关视觉提示
    document.getElementById("p-use-custom-address")?.addEventListener("change", (e) => {
        updateAddressFieldsState(Boolean(e.target.checked));
    });

    // 登录密码显隐
    document.getElementById("btn-p-toggle-pwd")?.addEventListener("click", () => {
        const pwdEl = document.getElementById("p-login-pwd");
        const icon = document.querySelector("#btn-p-toggle-pwd i");
        if (!pwdEl) return;
        if (pwdEl.type === "password") {
            pwdEl.type = "text";
            if (icon) icon.className = "fa-regular fa-eye-slash";
        } else {
            pwdEl.type = "password";
            if (icon) icon.className = "fa-regular fa-eye";
        }
    });

    // 账密登录
    document.getElementById("btn-p-login")?.addEventListener("click", loginMandarake);
    document.getElementById("p-login-pwd")?.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            loginMandarake();
        }
    });

    // 打开浏览器登录 & 复制提取命令 & 导入 Cookie
    document.getElementById("btn-p-launch-browser")?.addEventListener("click", launchBrowserLogin);
    document.getElementById("btn-p-copy-cookie-cmd")?.addEventListener("click", () => {
        const cmd = "copy(document.cookie)";
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(cmd).then(() => {
                showToast("已复制 Console 命令 `copy(document.cookie)`！请在官网登录后按 F12 粘贴执行", "success");
            }).catch(() => fallbackCopy(cmd));
        } else {
            fallbackCopy(cmd);
        }
    });
    document.getElementById("btn-p-import-cookies")?.addEventListener("click", importMandarakeCookies);

    // 刷新 / 检测登录态 / 退出登录
    document.getElementById("btn-p-check-auth")?.addEventListener("click", checkMandarakeLogin);
    document.getElementById("btn-p-refresh-auth")?.addEventListener("click", checkMandarakeLogin);
    document.getElementById("btn-p-logout")?.addEventListener("click", logoutMandarake);

    // 购物车查看与清空
    document.getElementById("btn-p-view-cart")?.addEventListener("click", viewMandarakeCart);
    document.getElementById("btn-p-clear-cart")?.addEventListener("click", clearMandarakeCart);

    // 保存收货地址与配送偏好
    document.getElementById("btn-save-purchase-address")?.addEventListener("click", savePurchaseAddress);

    // 保存自动抢购与支付设置
    document.getElementById("btn-save-purchase-settings")?.addEventListener("click", savePurchaseSettings);

    // 订单历史刷新 / 清空 / 实盘链路终步前测试
    document.getElementById("btn-refresh-purchase-orders")?.addEventListener("click", loadPurchaseOrders);
    document.getElementById("btn-clear-purchase-orders")?.addEventListener("click", clearPurchaseOrders);
    document.getElementById("btn-p-test-live-checkout")?.addEventListener("click", runLiveCheckoutTest);

    // 一键抢购弹窗绑定
    const qbModal = document.getElementById("quick-buy-modal");
    document.getElementById("btn-close-quick-buy")?.addEventListener("click", () => {
        qbModal?.classList.remove("active");
    });
    document.getElementById("btn-cancel-quick-buy")?.addEventListener("click", () => {
        qbModal?.classList.remove("active");
    });
    document.getElementById("btn-confirm-quick-buy")?.addEventListener("click", async () => {
        const item = state.purchase.pendingItem;
        if (!item) return;
        const paySelect = document.getElementById("qb-payment-select");
        const chosenPayment = paySelect ? paySelect.value : state.purchase.defaultPaymentMethod;
        const confirmBtn = document.getElementById("btn-confirm-quick-buy");
        await executeQuickBuy(item, chosenPayment, confirmBtn, () => {
            qbModal?.classList.remove("active");
        });
    });
}

async function parseJsonResponse(res) {
    const text = await res.text();
    try {
        return JSON.parse(text);
    } catch (_e) {
        if (res.status === 404) {
            throw new Error("后端接口未注册 (HTTP 404)：当前运行的后端仍是旧进程，请重启 python main.py（或重启 Docker 容器）后刷新页面重试。");
        }
        if (res.status >= 500) {
            throw new Error(`服务端内部错误 (HTTP ${res.status})，请查看【实时终端】或后端控制台日志。`);
        }
        throw new Error(`后端返回非 JSON 响应 (HTTP ${res.status})，请确认后端服务已重启至最新版本。`);
    }
}

async function loadPurchaseConfig() {
    try {
        const res = await fetch("/api/purchase/config");
        const data = await parseJsonResponse(res);
        if (!data.success) return;

        const cfg = data.config || {};
        const status = data.status || {};
        state.purchase.paymentMethods = data.payment_methods || [];
        state.purchase.excludedPaymentMethods = data.excluded_payment_methods || [];
        state.purchase.prefectures = data.prefectures || [];
        state.purchase.configLoaded = true;

        applyPurchaseConfigToState(cfg);
        populatePrefectures();
        renderPaymentMethodOptions();
        renderExcludedPayments();
        fillPurchaseFormInputs(cfg);
        updatePurchaseStatusUI(status);
        if (data.order_stats) {
            renderOrderStatsSummary(data.order_stats);
        }
    } catch (err) {
        console.error("加载抢购配置失败:", err);
    }
}

function applyPurchaseConfigToState(cfg) {
    if (!cfg) return;
    state.purchase.dryRun = cfg.dry_run !== false;
    state.purchase.autoBuyEnabled = Boolean(cfg.auto_buy_enabled);
    state.purchase.autoBuyScope = cfg.auto_buy_scope || "rule_enabled";
    state.purchase.autoBuyOnRestock = cfg.auto_buy_on_restock !== false;
    state.purchase.autoBuyMaxPrice = Number(cfg.auto_buy_max_price || 0);
    state.purchase.autoBuyMaxOrdersPerDay = Number(cfg.auto_buy_max_orders_per_day || 5);
    state.purchase.autoBuyCooldownSeconds = Number(cfg.auto_buy_cooldown_seconds || 3600);
    state.purchase.quickBuySkipConfirm = Boolean(cfg.quick_buy_skip_confirm);
    state.purchase.defaultPaymentMethod = cfg.default_payment_method || "bank";
    state.purchase.fallbackPaymentMethod = cfg.fallback_payment_method || "cod";
    state.purchase.useCustomAddress = Boolean(cfg.use_custom_address);
    state.purchase.address = cfg.address || {};
}

function populatePrefectures() {
    const sel = document.getElementById("p-addr-prefecture");
    if (!sel) return;
    const current = sel.value;
    let html = '<option value="">-- 保持账号默认 / 请选择都道府県 --</option>';
    (state.purchase.prefectures || []).forEach(pref => {
        html += `<option value="${escapeHtml(pref)}">${escapeHtml(pref)}</option>`;
    });
    sel.innerHTML = html;
    if (current) sel.value = current;
}

function renderPaymentMethodOptions() {
    const listEl = document.getElementById("p-payment-methods-list");
    const fallbackSel = document.getElementById("p-fallback-payment");
    const qbSelect = document.getElementById("qb-payment-select");
    const methods = state.purchase.paymentMethods || [];
    const currentDefault = state.purchase.defaultPaymentMethod || "bank";
    const currentFallback = state.purchase.fallbackPaymentMethod || "cod";

    if (listEl) {
        listEl.innerHTML = "";
        methods.forEach(m => {
            const isSelected = m.code === currentDefault;
            const card = document.createElement("label");
            card.className = `payment-option-card ${isSelected ? "selected" : ""}`;
            card.innerHTML = `
                <div class="payment-option-top">
                    <span class="payment-option-radio-wrap">
                        <input type="radio" name="p-default-payment" value="${escapeHtml(m.code)}" ${isSelected ? "checked" : ""}>
                        <span>${escapeHtml(m.label)}</span>
                    </span>
                    <span class="payment-option-badge">${escapeHtml(m.badge || "免认证")}</span>
                </div>
                <div class="payment-option-summary">${escapeHtml(m.summary || "")}</div>
                <div class="payment-option-caveat"><i class="fa-solid fa-circle-info"></i> ${escapeHtml(m.caveats || "")}</div>
            `;
            const radio = card.querySelector("input[type='radio']");
            radio?.addEventListener("change", () => {
                state.purchase.defaultPaymentMethod = m.code;
                state.purchase.defaultPaymentLabel = m.label;
                listEl.querySelectorAll(".payment-option-card").forEach(c => c.classList.remove("selected"));
                card.classList.add("selected");
            });
            listEl.appendChild(card);
        });
    }

    if (fallbackSel) {
        let fbHtml = '<option value="none">不降级（主选不可用则安全中止）</option>';
        methods.forEach(m => {
            fbHtml += `<option value="${escapeHtml(m.code)}">${escapeHtml(m.label)}</option>`;
        });
        fallbackSel.innerHTML = fbHtml;
        fallbackSel.value = currentFallback;
    }

    if (qbSelect) {
        let qbHtml = "";
        methods.forEach(m => {
            qbHtml += `<option value="${escapeHtml(m.code)}">${escapeHtml(m.label)}</option>`;
        });
        qbSelect.innerHTML = qbHtml;
        qbSelect.value = currentDefault;
    }
}

function renderExcludedPayments() {
    const container = document.getElementById("p-excluded-payments-list");
    if (!container) return;
    const excluded = state.purchase.excludedPaymentMethods || [];
    if (excluded.length === 0) {
        container.innerHTML = "";
        return;
    }
    container.innerHTML = excluded.map(item => `
        <div class="excluded-payment-row">
            <strong>🚫 ${escapeHtml(item.name || "")}</strong>
            <span>${escapeHtml(item.reason || "")}</span>
        </div>
    `).join("");
}

function fillPurchaseFormInputs(cfg) {
    const loginId = document.getElementById("p-login-id");
    if (loginId) loginId.value = cfg.username || "";
    const loginPwd = document.getElementById("p-login-pwd");
    if (loginPwd && cfg.has_saved_password && !loginPwd.value) {
        loginPwd.placeholder = "已保存登录密码（留空保持不变）";
    }
    const saveCreds = document.getElementById("p-save-creds");
    if (saveCreds) saveCreds.checked = Boolean(cfg.save_credentials);

    const dryRunEl = document.getElementById("p-dry-run");
    if (dryRunEl) dryRunEl.checked = cfg.dry_run !== false;
    const topDryRunEl = document.getElementById("p-top-dry-run-toggle");
    if (topDryRunEl) topDryRunEl.checked = cfg.dry_run !== false;
    updatePurchaseModeDecorators(cfg.dry_run !== false);

    const autoBuyEl = document.getElementById("p-auto-buy-enabled");
    if (autoBuyEl) autoBuyEl.checked = Boolean(cfg.auto_buy_enabled);
    const topAutoBuyEl = document.getElementById("p-top-auto-buy-toggle");
    if (topAutoBuyEl) topAutoBuyEl.checked = Boolean(cfg.auto_buy_enabled);

    const scopeEl = document.getElementById("p-auto-buy-scope");
    if (scopeEl) scopeEl.value = cfg.auto_buy_scope || "rule_enabled";
    const maxPriceEl = document.getElementById("p-auto-buy-max-price");
    if (maxPriceEl) maxPriceEl.value = cfg.auto_buy_max_price || 0;
    const maxDailyEl = document.getElementById("p-auto-buy-max-daily");
    if (maxDailyEl) maxDailyEl.value = cfg.auto_buy_max_orders_per_day || 5;
    const cooldownEl = document.getElementById("p-auto-buy-cooldown");
    if (cooldownEl) cooldownEl.value = cfg.auto_buy_cooldown_seconds || 3600;
    const restockEl = document.getElementById("p-auto-buy-on-restock");
    if (restockEl) restockEl.checked = cfg.auto_buy_on_restock !== false;
    const skipConfirmEl = document.getElementById("p-quick-buy-skip-confirm");
    if (skipConfirmEl) skipConfirmEl.checked = Boolean(cfg.quick_buy_skip_confirm);

    const useCustomAddrEl = document.getElementById("p-use-custom-address");
    if (useCustomAddrEl) useCustomAddrEl.checked = Boolean(cfg.use_custom_address);
    updateAddressFieldsState(Boolean(cfg.use_custom_address));

    const addr = cfg.address || {};
    const fieldMap = {
        "p-addr-last-name": addr.last_name || "",
        "p-addr-first-name": addr.first_name || "",
        "p-addr-last-kana": addr.last_name_kana || "",
        "p-addr-first-kana": addr.first_name_kana || "",
        "p-addr-postal": addr.postal_code || "",
        "p-addr-prefecture": addr.prefecture || "",
        "p-addr-address1": addr.address1 || "",
        "p-addr-address2": addr.address2 || "",
        "p-addr-phone": addr.phone || "",
        "p-addr-email": addr.email || "",
        "p-addr-delivery-method": addr.delivery_method || "auto",
        "p-addr-delivery-time": addr.delivery_time || "none",
        "p-addr-remarks": addr.remarks || "",
    };
    Object.entries(fieldMap).forEach(([elId, val]) => {
        const el = document.getElementById(elId);
        if (el) el.value = val;
    });
}

function updateAddressFieldsState(useCustom) {
    const wrap = document.getElementById("p-address-fields-wrap");
    if (wrap) {
        wrap.classList.toggle("address-fields-dimmed", !useCustom);
    }
}

function updatePurchaseModeDecorators(isDry) {
    const topLabel = document.getElementById("p-top-dry-run-label");
    if (topLabel) {
        topLabel.textContent = isDry ? "🛡️ Dry-Run 仿真演练" : "🚀 LIVE 实盘下单";
        topLabel.style.color = isDry ? "#60a5fa" : "#f87171";
    }
    const modeBadge = document.getElementById("p-mode-badge");
    if (modeBadge) {
        modeBadge.textContent = isDry ? "仿真演练保护中" : "⚠️ 实盘下单生效中";
        modeBadge.className = isDry ? "badge-tag stock" : "badge-tag soldout";
    }
}

function updatePurchaseStatusUI(status) {
    if (!status) return;
    state.purchase.isLoggedIn = Boolean(status.is_logged_in);
    state.purchase.accountName = status.account_name || "";
    state.purchase.dryRun = status.dry_run !== false;
    state.purchase.autoBuyEnabled = Boolean(status.auto_buy_enabled);
    state.purchase.quickBuySkipConfirm = Boolean(status.quick_buy_skip_confirm);
    state.purchase.defaultPaymentMethod = status.default_payment_method || state.purchase.defaultPaymentMethod || "bank";
    state.purchase.defaultPaymentLabel = status.default_payment_label || state.purchase.defaultPaymentLabel || "銀行振込";
    state.purchase.useCustomAddress = Boolean(status.use_custom_address);

    // 顶部 Header 抢购药丸
    const headerDot = document.getElementById("purchase-header-dot");
    const headerText = document.getElementById("purchase-header-text");
    if (headerDot) {
        headerDot.className = `status-dot ${state.purchase.isLoggedIn ? "connected" : "disconnected"}`;
    }
    if (headerText) {
        const authTxt = state.purchase.isLoggedIn ? `已登录: ${state.purchase.accountName || "会员"}` : "未登录";
        const modeTxt = state.purchase.dryRun ? "演练" : "实盘";
        const autoTxt = state.purchase.autoBuyEnabled ? " | ⚡自动抢" : "";
        headerText.textContent = `${authTxt} | ${modeTxt}${autoTxt}`;
    }

    // 抢购页顶部栏
    const topDot = document.getElementById("p-top-dot");
    const topTitle = document.getElementById("p-top-login-title");
    const topMeta = document.getElementById("p-top-login-meta");
    const authBadge = document.getElementById("p-auth-badge");
    const payPill = document.getElementById("p-top-payment-pill");
    const topDry = document.getElementById("p-top-dry-run-toggle");
    const topAuto = document.getElementById("p-top-auto-buy-toggle");
    const topAutoLabel = document.getElementById("p-top-auto-buy-label");

    if (topDot) topDot.className = `status-indicator-dot ${state.purchase.isLoggedIn ? "" : "stopped"}`;
    if (topTitle) {
        topTitle.textContent = state.purchase.isLoggedIn
            ? `已登录 Mandarake: ${state.purchase.accountName || "会员"}`
            : "尚未登录 Mandarake 账号";
    }
    if (topMeta) {
        topMeta.textContent = status.last_login_message || "请先登录或导入 Cookie";
    }
    if (authBadge) {
        authBadge.textContent = state.purchase.isLoggedIn ? "已登录" : "未登录";
        authBadge.style.background = state.purchase.isLoggedIn ? "var(--success)" : "var(--danger)";
    }
    if (payPill) {
        payPill.innerHTML = `<i class="fa-solid fa-credit-card"></i> 默认支付: ${escapeHtml(state.purchase.defaultPaymentLabel)}`;
    }
    if (topDry) topDry.checked = state.purchase.dryRun;
    const cardDry = document.getElementById("p-dry-run");
    if (cardDry) cardDry.checked = state.purchase.dryRun;
    if (topAuto) topAuto.checked = state.purchase.autoBuyEnabled;
    const cardAuto = document.getElementById("p-auto-buy-enabled");
    if (cardAuto) cardAuto.checked = state.purchase.autoBuyEnabled;
    if (topAutoLabel) {
        topAutoLabel.textContent = state.purchase.autoBuyEnabled ? "⚡ 自动抢购: 开" : "⚡ 自动抢购: 关";
        topAutoLabel.style.color = state.purchase.autoBuyEnabled ? "#fbbf24" : "var(--text-secondary)";
    }
    updatePurchaseModeDecorators(state.purchase.dryRun);
}

async function savePurchaseConfigPartial(partialPayload, successToastMsg = "设置已保存") {
    try {
        const res = await fetch("/api/purchase/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(partialPayload)
        });
        const data = await parseJsonResponse(res);
        if (data.success) {
            if (data.config) applyPurchaseConfigToState(data.config);
            if (data.status) updatePurchaseStatusUI(data.status);
            showToast(successToastMsg, "success");
        } else {
            showToast(data.message || "保存失败", "error");
        }
        return data;
    } catch (err) {
        showToast("保存购买配置异常: " + err, "error");
        return null;
    }
}

async function savePurchaseSettings() {
    const selectedRadio = document.querySelector("input[name='p-default-payment']:checked");
    const defaultPay = selectedRadio ? selectedRadio.value : (state.purchase.defaultPaymentMethod || "bank");
    const fallbackPay = document.getElementById("p-fallback-payment")?.value || "cod";
    const dryRun = document.getElementById("p-dry-run")?.checked !== false;
    if (state.purchase.dryRun && !dryRun) {
        if (!confirm("⚠️ 确定关闭 Dry-Run 仿真演练、切换为【LIVE 实盘下单模式】吗？\n切换后一键抢购与自动抢购将会向 Mandarake 提交真实订单！")) {
            const cardDry = document.getElementById("p-dry-run");
            if (cardDry) cardDry.checked = true;
            const topDry = document.getElementById("p-top-dry-run-toggle");
            if (topDry) topDry.checked = true;
            updatePurchaseModeDecorators(true);
            return;
        }
    }
    const autoBuyEnabled = Boolean(document.getElementById("p-auto-buy-enabled")?.checked);
    const scope = document.getElementById("p-auto-buy-scope")?.value || "rule_enabled";
    const maxPrice = Math.max(0, parseInt(document.getElementById("p-auto-buy-max-price")?.value, 10) || 0);
    const maxDaily = Math.max(1, parseInt(document.getElementById("p-auto-buy-max-daily")?.value, 10) || 5);
    const cooldown = Math.max(10, parseInt(document.getElementById("p-auto-buy-cooldown")?.value, 10) || 3600);
    const onRestock = document.getElementById("p-auto-buy-on-restock")?.checked !== false;
    const skipConfirm = Boolean(document.getElementById("p-quick-buy-skip-confirm")?.checked);

    const payload = {
        dry_run: dryRun,
        auto_buy_enabled: autoBuyEnabled,
        auto_buy_scope: scope,
        auto_buy_max_price: maxPrice,
        auto_buy_max_orders_per_day: maxDaily,
        auto_buy_cooldown_seconds: cooldown,
        auto_buy_on_restock: onRestock,
        quick_buy_skip_confirm: skipConfirm,
        default_payment_method: defaultPay,
        fallback_payment_method: fallbackPay,
    };

    const data = await savePurchaseConfigPartial(payload, "自动抢购与默认支付设置已保存生效！");
    if (data && data.config) {
        fillPurchaseFormInputs(data.config);
        renderPaymentMethodOptions();
    }
}

async function savePurchaseAddress() {
    let useCustom = Boolean(document.getElementById("p-use-custom-address")?.checked);
    const address = {
        last_name: document.getElementById("p-addr-last-name")?.value.trim() || "",
        first_name: document.getElementById("p-addr-first-name")?.value.trim() || "",
        last_name_kana: document.getElementById("p-addr-last-kana")?.value.trim() || "",
        first_name_kana: document.getElementById("p-addr-first-kana")?.value.trim() || "",
        postal_code: document.getElementById("p-addr-postal")?.value.trim() || "",
        prefecture: document.getElementById("p-addr-prefecture")?.value.trim() || "",
        address1: document.getElementById("p-addr-address1")?.value.trim() || "",
        address2: document.getElementById("p-addr-address2")?.value.trim() || "",
        phone: document.getElementById("p-addr-phone")?.value.trim() || "",
        email: document.getElementById("p-addr-email")?.value.trim() || "",
        delivery_method: document.getElementById("p-addr-delivery-method")?.value || "auto",
        delivery_time: document.getElementById("p-addr-delivery-time")?.value || "none",
        remarks: document.getElementById("p-addr-remarks")?.value || "",
    };

    if (!useCustom && (address.last_name || address.postal_code || address.address1 || address.phone)) {
        if (confirm("检测到您已填写收货地址信息，但尚未勾选「启用自定义地址覆盖」。\n是否同时开启自定义地址覆盖，使下单时自动填入这些信息？")) {
            useCustom = true;
            const customChk = document.getElementById("p-use-custom-address");
            if (customChk) customChk.checked = true;
            updateAddressFieldsState(true);
        }
    }

    if (useCustom && (!address.last_name || !address.postal_code || !address.address1 || !address.phone)) {
        if (!confirm("已勾选「启用自定义地址覆盖」，但姓/邮编/市区町村/电话尚未填全（空字段会保留官网默认值）。确认继续保存吗？")) {
            return;
        }
    }

    await savePurchaseConfigPartial(
        { use_custom_address: useCustom, address: address },
        useCustom ? "自定义收货地址与配送偏好已保存！" : "配送偏好已保存（当前使用会员默认收货地址）"
    );
}

async function loginMandarake() {
    const username = document.getElementById("p-login-id")?.value.trim() || "";
    const password = document.getElementById("p-login-pwd")?.value || "";
    const saveCreds = Boolean(document.getElementById("p-save-creds")?.checked);
    const btn = document.getElementById("btn-p-login");
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> 正在尝试登录...`;
    }
    try {
        const res = await fetch("/api/purchase/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password, save_credentials: saveCreds })
        });
        const data = await parseJsonResponse(res);
        if (data.status) updatePurchaseStatusUI(data.status);
        if (data.success) {
            showToast(data.message || "Mandarake 登录成功！", "success");
        } else {
            showToast(data.message || "登录失败", "warning", 6000);
            if (data.requires_browser_login) {
                document.querySelector(".subtab-btn[data-subtab='pane-login-cookie']")?.click();
            }
        }
    } catch (err) {
        showToast("登录请求异常: " + err, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `<i class="fa-solid fa-right-to-bracket"></i> 登录 Mandarake 账号`;
        }
    }
}

async function launchBrowserLogin() {
    try {
        const res = await fetch("/api/purchase/auth/launch_browser", { method: "POST" });
        const data = await parseJsonResponse(res);
        if (data.login_url && !data.success) {
            window.open(data.login_url, "_blank", "noopener,noreferrer");
        }
        showToast(data.message || "已准备官网登录页面", data.success ? "success" : "info", 6000);
    } catch (err) {
        window.open("https://my.mandarake.co.jp/customer/login/?lc=ja", "_blank", "noopener,noreferrer");
    }
}

async function importMandarakeCookies() {
    const cookieStr = document.getElementById("p-cookie-raw")?.value.trim() || "";
    const sessionId = document.getElementById("p-cookie-session-id")?.value.trim() || "";
    const userId = document.getElementById("p-cookie-user-id")?.value.trim() || "";

    if (!cookieStr && (!sessionId || !userId)) {
        showToast("请粘贴完整 Cookie 字符串，或填写 mandarake_sessionId 与 mandarake_userId", "warning");
        return;
    }

    const btn = document.getElementById("btn-p-import-cookies");
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> 正在在线验证 Cookie...`;
    }
    try {
        const res = await fetch("/api/purchase/auth/import_cookies", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ cookie_str: cookieStr, session_id: sessionId, user_id: userId, verify_online: true })
        });
        const data = await parseJsonResponse(res);
        if (data.status) updatePurchaseStatusUI(data.status);
        showToast(data.message || (data.success ? "Cookie 导入成功！" : "Cookie 验证失败"), data.success ? "success" : "error", 5000);
    } catch (err) {
        showToast("导入 Cookie 异常: " + (err?.message || String(err)), "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `<i class="fa-solid fa-file-import"></i> 导入 Cookie 并在线验活`;
        }
    }
}

async function checkMandarakeLogin() {
    try {
        const res = await fetch("/api/purchase/auth/check", { method: "POST" });
        const data = await parseJsonResponse(res);
        if (data.status) updatePurchaseStatusUI(data.status);
        showToast(data.message || (data.is_logged_in ? "登录态有效" : "尚未登录或会话已过期"), data.is_logged_in ? "success" : "warning");
    } catch (err) {
        showToast("检测登录态异常: " + (err?.message || String(err)), "error");
    }
}

async function logoutMandarake() {
    if (!confirm("确定退出当前登录的 Mandarake 账号并清除会话 Cookie 吗？")) return;
    try {
        const res = await fetch("/api/purchase/auth/logout", { method: "POST" });
        const data = await parseJsonResponse(res);
        if (data.status) updatePurchaseStatusUI(data.status);
        showToast(data.message || "已退出登录", "info");
    } catch (err) {
        showToast("退出登录异常: " + (err?.message || String(err)), "error");
    }
}

async function viewMandarakeCart() {
    try {
        const res = await fetch("/api/purchase/cart");
        const data = await parseJsonResponse(res);
        if (!data.success) {
            showToast(data.message || "读取购物车失败", "error");
            return;
        }
        if (!data.items || data.items.length === 0) {
            showToast("当前官网购物车为空 (0 件)", "info");
        } else {
            const lines = data.items.map(it => `• [${it.id}] ${it.title} ${it.price_str}`).join("\n");
            alert(`当前 Mandarake 购物车共 ${data.count} 件商品：\n\n${lines}`);
        }
    } catch (err) {
        showToast("查询购物车异常: " + (err?.message || String(err)), "error");
    }
}

async function clearMandarakeCart() {
    if (!confirm("确定清空当前 Mandarake 官网账号购物车中的所有商品吗？")) return;
    try {
        const res = await fetch("/api/purchase/cart/clear", { method: "POST" });
        const data = await parseJsonResponse(res);
        showToast(data.message || "已清空购物车", data.success ? "success" : "error");
    } catch (err) {
        showToast("清空购物车异常: " + (err?.message || String(err)), "error");
    }
}

// --- 商品阅览 / 上新记录 一键抢购 ---

async function handleOneClickBuy(item, triggerBtn = null) {
    if (!state.purchase.configLoaded) {
        await loadPurchaseConfig();
    }
    if (state.purchase.quickBuySkipConfirm && state.purchase.isLoggedIn) {
        await executeQuickBuy(item, state.purchase.defaultPaymentMethod, triggerBtn);
        return;
    }
    openQuickBuyModal(item);
}

function openQuickBuyModal(item) {
    const modal = document.getElementById("quick-buy-modal");
    if (!modal) return;
    state.purchase.pendingItem = item;

    const imgEl = document.getElementById("qb-item-img");
    if (imgEl) {
        imgEl.referrerPolicy = "no-referrer";
        imgEl.onerror = () => {
            imgEl.onerror = null;
            imgEl.src = DEFAULT_ITEM_THUMB;
        };
        imgEl.src = item.img_url || DEFAULT_ITEM_THUMB;
    }
    const titleEl = document.getElementById("qb-item-title");
    if (titleEl) titleEl.textContent = item.title || `商品 ${item.id}`;
    const priceEl = document.getElementById("qb-item-price");
    if (priceEl) priceEl.textContent = item.price ? `${Number(item.price).toLocaleString()}円` : (item.price_str || "价格未知");
    const shopEl = document.getElementById("qb-item-shop");
    if (shopEl) shopEl.textContent = item.shop || "まんだらけ";
    const idEl = document.getElementById("qb-item-id");
    if (idEl) idEl.textContent = `商品ID: ${item.id}`;

    const authBanner = document.getElementById("qb-auth-banner");
    if (authBanner) {
        if (state.purchase.isLoggedIn) {
            authBanner.className = "qb-banner ok";
            authBanner.innerHTML = `<i class="fa-solid fa-circle-check"></i> 当前已登录账号：<strong>${escapeHtml(state.purchase.accountName || "会员")}</strong>`;
        } else {
            authBanner.className = "qb-banner danger";
            authBanner.innerHTML = `
                <i class="fa-solid fa-triangle-exclamation"></i>
                <span style="flex:1;">当前尚未登录！请先前往【抢购设置】登录或导入 Cookie 后再下单。</span>
                <button type="button" class="btn btn-primary btn-xs" id="btn-qb-goto-login">前往登录 →</button>
            `;
            authBanner.querySelector("#btn-qb-goto-login")?.addEventListener("click", () => {
                modal.classList.remove("active");
                document.querySelector('[data-tab="tab-purchase"]')?.click();
            });
        }
    }

    const modeBanner = document.getElementById("qb-mode-banner");
    if (modeBanner) {
        if (state.purchase.dryRun) {
            modeBanner.className = "qb-banner ok";
            modeBanner.innerHTML = `<i class="fa-solid fa-shield-halved"></i> 当前处于 <strong>Dry-Run 仿真演练模式</strong>：验证至确认页后将安全拦截并清理购物车，不产生真实扣款。`;
        } else {
            modeBanner.className = "qb-banner warn";
            modeBanner.innerHTML = `<i class="fa-solid fa-fire"></i> 当前处于 <strong>LIVE 实盘下单模式</strong>：点击确定将向 Mandarake 提交真实购买订单！`;
        }
    }

    const paySelect = document.getElementById("qb-payment-select");
    if (paySelect) {
        if (paySelect.options.length === 0 && (state.purchase.paymentMethods || []).length > 0) {
            renderPaymentMethodOptions();
        }
        paySelect.value = state.purchase.defaultPaymentMethod || "bank";
    }

    const addrSummary = document.getElementById("qb-address-summary");
    if (addrSummary) {
        if (state.purchase.useCustomAddress) {
            const a = state.purchase.address || {};
            const name = `${a.last_name || ""} ${a.first_name || ""}`.trim() || "未填姓名";
            const loc = `${a.postal_code || ""} ${a.prefecture || ""}${a.address1 || ""}`.trim() || "未填地址";
            addrSummary.innerHTML = `<strong>收货地址（自定义覆盖）：</strong>${escapeHtml(name)} | ${escapeHtml(loc)} | ${escapeHtml(a.phone || "")}`;
        } else {
            addrSummary.innerHTML = `<strong>收货地址：</strong>自动沿用 Mandarake 官网会员中心已登记默认地址`;
        }
    }

    modal.classList.add("active");
}

async function executeQuickBuy(item, paymentMethod, triggerBtn = null, onAfterClose = null) {
    const origHtml = triggerBtn ? triggerBtn.innerHTML : "";
    if (triggerBtn) {
        triggerBtn.disabled = true;
        triggerBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> 抢购中...`;
    }

    try {
        const res = await fetch("/api/purchase/buy", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                item_id: item.id,
                title: item.title || "",
                price: item.price || 0,
                img_url: item.img_url || "",
                shop: item.shop || "",
                dry_run: state.purchase.dryRun,
                payment_method: paymentMethod || state.purchase.defaultPaymentMethod,
            })
        });
        const data = await parseJsonResponse(res);
        if (onAfterClose) onAfterClose();
        if (data.purchase) updatePurchaseStatusUI(data.purchase);

        if (data.status === "SUCCESS") {
            showToast(data.message || "实盘抢购成功！", "success", 6000);
            markProductCardSoldOut(item.id);
        } else if (data.status === "DRY_RUN") {
            showToast(data.message || "Dry-Run 仿真演练通过！", "info", 6000);
        } else if (data.status === "NEED_PAYMENT_AUTH") {
            showToast(data.message || "需要打开外部链接完成支付认证", "warning", 8000);
        } else {
            showToast(data.message || "抢购失败", "error", 6000);
        }

        if (state.activeTab === "tab-purchase") {
            loadPurchaseOrders();
        }
    } catch (err) {
        showToast("一键抢购请求异常: " + (err?.message || String(err)), "error");
    } finally {
        if (triggerBtn) {
            const isProductBuyBtn = triggerBtn.classList.contains("btn-card-quick-buy");
            const cardEl = isProductBuyBtn ? document.querySelector(`.product-card[data-item-id="${item.id}"]`) : null;
            if (!isProductBuyBtn || !cardEl || !cardEl.classList.contains("is-soldout")) {
                triggerBtn.disabled = false;
                triggerBtn.innerHTML = origHtml || `<i class="fa-solid fa-bolt"></i> 一键抢购`;
            }
        }
    }
}

function markProductCardSoldOut(itemId) {
    const cardEl = document.querySelector(`.product-card[data-item-id="${itemId}"]`);
    if (!cardEl) return;
    cardEl.classList.add("is-soldout");
    const badgeWrap = cardEl.querySelector(".card-badges");
    if (badgeWrap) {
        badgeWrap.innerHTML = '<span class="badge-tag soldout">🔴 売り切れ</span>';
    }
    const buyBtn = cardEl.querySelector(".btn-card-quick-buy");
    if (buyBtn) {
        buyBtn.disabled = true;
        buyBtn.innerHTML = '<i class="fa-solid fa-bolt"></i> 已售罄';
        buyBtn.title = "该商品已售罄";
    }
}

// --- 抢购订单历史管理 ---

async function loadPurchaseOrders() {
    try {
        const res = await fetch("/api/purchase/orders?limit=50");
        const data = await parseJsonResponse(res);
        if (!data.success) return;
        renderPurchaseOrders(data.orders || []);
        if (data.stats) renderOrderStatsSummary(data.stats);
    } catch (err) {
        console.debug("加载抢购历史异常:", err);
    }
}

function renderOrderStatsSummary(stats) {
    const el = document.getElementById("p-orders-stats");
    if (!el || !stats) return;
    el.textContent = `累计 ${stats.total || 0} 单 | 今日 ${stats.today_count || 0} 单 | 实盘成功 ${stats.success_count || 0} | 演练 ${stats.dry_run_count || 0} | 失败 ${stats.failed_count || 0}`;
}

function renderPurchaseOrders(orders) {
    const container = document.getElementById("purchase-orders-list");
    if (!container) return;

    if (!orders || orders.length === 0) {
        container.innerHTML = `<div class="empty-orders-tip">暂无抢购记录，前往【商品阅览】点击「⚡ 一键抢购」或开启自动抢购即可体验！</div>`;
        return;
    }

    const statusBadgeMap = {
        "SUCCESS": { cls: "status-success", text: "✅ 实盘成功" },
        "DRY_RUN": { cls: "status-dry-run", text: "🛡️ 仿真演练" },
        "NEED_PAYMENT_AUTH": { cls: "status-need-auth", text: "🔐 待支付认证" },
        "FAILED": { cls: "status-failed", text: "❌ 失败" },
    };

    container.innerHTML = "";
    orders.forEach(ord => {
        const stInfo = statusBadgeMap[ord.status] || { cls: "status-failed", text: ord.status || "未知" };
        const imgUrl = ord.img_url || DEFAULT_ITEM_THUMB;
        const itemUrl = ord.url || `https://order.mandarake.co.jp/order/detailPage/item?itemCode=${ord.item_id}`;
        let triggerLabel = "🖱️ 一键抢购";
        if (ord.trigger_type === "auto") {
            triggerLabel = `⚡ 自动抢购${ord.rule_name ? ` (${ord.rule_name})` : ""}`;
        } else if (ord.trigger_type === "live_test") {
            triggerLabel = "🧪 实盘终步前测试";
        }
        const payLabel = ord.payment_label || ord.payment_method || "-";
        const timeStr = formatTimestamp(ord.created_at);

        const row = document.createElement("div");
        row.className = "order-history-item";
        row.innerHTML = `
            <img class="order-history-thumb" src="${escapeHtml(imgUrl)}" alt="" referrerpolicy="no-referrer" onerror="this.onerror=null;this.src='/static/icon-192.png'">
            <div class="order-history-body">
                <div class="order-history-head">
                    <a class="order-history-title" href="${escapeHtml(itemUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(ord.title || `商品 ${ord.item_id}`)}</a>
                    <span class="order-status-pill ${stInfo.cls}">${stInfo.text}</span>
                </div>
                <div class="order-history-meta">
                    <span><i class="fa-solid fa-yen-sign"></i> ${ord.price ? `${Number(ord.price).toLocaleString()}円` : (escapeHtml(ord.price_str) || "-")}</span>
                    <span><i class="fa-solid fa-wallet"></i> ${escapeHtml(payLabel)}</span>
                    <span>${escapeHtml(triggerLabel)}</span>
                    <span><i class="fa-regular fa-clock"></i> ${escapeHtml(timeStr)} (${ord.duration_ms || 0}ms)</span>
                </div>
                <div class="order-history-msg">${escapeHtml(ord.message || "")}</div>
                ${ord.payment_url ? `<div class="mt-3"><a class="btn btn-warning btn-xs" href="${escapeHtml(ord.payment_url)}" target="_blank" rel="noopener noreferrer"><i class="fa-solid fa-arrow-up-right-from-square"></i> 前往完成支付认证</a></div>` : ""}
            </div>
            <div class="order-history-actions">
                <button type="button" class="btn btn-outline-danger btn-xs btn-del-order" title="删除此条记录"><i class="fa-solid fa-trash-can"></i></button>
            </div>
        `;
        row.querySelector(".btn-del-order")?.addEventListener("click", () => deletePurchaseOrder(ord.id));
        container.appendChild(row);
    });
}

async function deletePurchaseOrder(orderId) {
    try {
        const res = await fetch(`/api/purchase/orders/${encodeURIComponent(orderId)}`, { method: "DELETE" });
        const data = await parseJsonResponse(res);
        if (data.success) {
            loadPurchaseOrders();
        }
    } catch (err) {
        showToast("删除记录失败: " + (err?.message || String(err)), "error");
    }
}

async function clearPurchaseOrders() {
    if (!confirm("确定清空全部抢购 / 下单历史记录吗？")) return;
    try {
        const res = await fetch("/api/purchase/orders/clear", { method: "POST" });
        const data = await parseJsonResponse(res);
        if (data.success) {
            showToast("已清空抢购历史记录", "info");
            loadPurchaseOrders();
        }
    } catch (err) {
        showToast("清空失败: " + (err?.message || String(err)), "error");
    }
}

function handleOrderUpdatedEvent(order) {
    if (!order) return;
    if (state.activeTab === "tab-purchase") {
        loadPurchaseOrders();
    } else {
        const badge = document.getElementById("purchase-orders-badge");
        if (badge) {
            state.purchase.unreadOrders = (state.purchase.unreadOrders || 0) + 1;
            badge.textContent = state.purchase.unreadOrders;
            badge.style.display = "inline-flex";
        }
    }
    if (order.trigger_type === "auto") {
        const type = order.status === "SUCCESS" ? "success" : (order.status === "DRY_RUN" ? "info" : "warning");
        showToast(`⚡ 自动抢购结果 [${order.status}]: ${order.title || order.item_id}`, type, 5000);
    }
    if (order.status === "SUCCESS") {
        markProductCardSoldOut(order.item_id);
    }
}

function handleOrdersClearedEvent() {
    if (state.activeTab === "tab-purchase") {
        loadPurchaseOrders();
    }
}

function clearPurchaseOrdersBadge() {
    state.purchase.unreadOrders = 0;
    const badge = document.getElementById("purchase-orders-badge");
    if (badge) {
        badge.style.display = "none";
    }
}

async function runLiveCheckoutTest() {
    const btn = document.getElementById("btn-p-test-live-checkout");
    const inputEl = document.getElementById("p-live-test-item-id");
    const resultEl = document.getElementById("p-live-test-result");
    const itemInput = inputEl ? inputEl.value.trim() : "";

    const selectedRadio = document.querySelector("input[name='p-default-payment']:checked");
    const paymentMethod = selectedRadio ? selectedRadio.value : state.purchase.defaultPaymentMethod;

    const origHtml = btn ? btn.innerHTML : "";
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> 正在实盘链路测试...`;
    }
    if (resultEl) {
        resultEl.style.display = "block";
        resultEl.className = "live-test-result";
        resultEl.innerHTML = `<div class="live-test-summary"><i class="fa-solid fa-spinner fa-spin"></i> 正在校验登录态、加购并模拟推进至订单确认页（最后一步下单前自动拦截）...</div>`;
    }

    try {
        const res = await fetch("/api/purchase/test_live_checkout", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                item_id: itemInput,
                payment_method: paymentMethod
            })
        });
        const data = await parseJsonResponse(res);
        if (data.purchase) updatePurchaseStatusUI(data.purchase);

        const ok = Boolean(data.success);
        const steps = Array.isArray(data.steps) ? data.steps : [];
        const tested = data.tested_item || null;
        const testedLine = tested
            ? `<div style="margin-bottom:6px;color:var(--text-secondary);">测试商品：<strong>${escapeHtml(tested.title || tested.id)}</strong> (ID: ${escapeHtml(tested.id)}${tested.price ? ` · ${Number(tested.price).toLocaleString()}円` : ""})</div>`
            : "";

        const stepsHtml = steps.map(st => `
            <div class="live-test-step ${st.ok ? "ok" : "fail"}">
                <i class="fa-solid ${st.ok ? "fa-circle-check" : "fa-circle-xmark"}"></i>
                <span><strong>${escapeHtml(st.label || st.step || "")}：</strong>${escapeHtml(st.detail || "")}</span>
            </div>
        `).join("");

        if (resultEl) {
            resultEl.style.display = "block";
            resultEl.className = `live-test-result ${ok ? "ok" : "err"}`;
            resultEl.innerHTML = `
                <div class="live-test-summary">${ok ? "✅ " : "❌ "}${escapeHtml(data.message || (ok ? "实盘终步前链路测试通过" : "测试未通过"))}</div>
                ${testedLine}
                <div class="live-test-steps">${stepsHtml}</div>
            `;
        }

        showToast(data.message || (ok ? "实盘抢购终步前测试成功！" : "测试失败"), ok ? "success" : "error", 6000);
        loadPurchaseOrders();
    } catch (err) {
        const errMsg = err?.message || String(err);
        if (resultEl) {
            resultEl.style.display = "block";
            resultEl.className = "live-test-result err";
            resultEl.innerHTML = `<div class="live-test-summary">❌ ${escapeHtml(errMsg)}</div>`;
        }
        showToast(errMsg, "error", 7000);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = origHtml || `<i class="fa-solid fa-flask-vial"></i> 实盘抢购测试（终步前停止）`;
        }
    }
}
