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

    isLoggedIn: false,
    accountName: "",
    dryRun: true,
    
    selectedCategoriesForCrawl: [],
    lastLogId: 0,
    activeTab: "tab-products",
    
    // 当前待下单暂存
    pendingOrderItem: null,
};

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
    loadCategories();
    loadAuthStatus();
    initPresetKeywords();
    loadProducts();
    loadStats();
    loadOrders();
    loadMonitorConfig();
    initEventListeners();
    
    // 定时轮询统计与状态 (每 10 秒)
    setInterval(() => {
        loadStats();
        if (state.activeTab === "tab-orders") {
            loadOrders();
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

            if (targetId === "tab-products") {
                loadProducts();
            } else if (targetId === "tab-orders") {
                loadOrders();
            } else if (targetId === "tab-crawler") {
                loadCrawlerStatus();
            } else if (targetId === "tab-monitor") {
                loadMonitorConfig();
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

    // 智能分类归整按钮
    document.getElementById("btn-reclassify-cats")?.addEventListener("click", async () => {
        const btn = document.getElementById("btn-reclassify-cats");
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> 正在规整细分...`;
        }
        try {
            const res = await fetch("/api/categories/reclassify", { method: "POST" });
            const data = await res.json();
            alert(data.message || "分类规整完成！");
            loadProducts();
            loadStats();
        } catch (err) {
            alert("智能分类规整异常: " + err);
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = `<i class="fa-solid fa-wand-magic-sparkles"></i> 智能归整细分`;
            }
        }
    });

    // 7. 快捷前往爬虫页
    document.getElementById("btn-quick-goto-crawler")?.addEventListener("click", () => {
        document.querySelector("[data-tab='tab-crawler']")?.click();
    });

    // 8. Dry-Run 模式开关
    const dryRunToggle = document.getElementById("dry-run-toggle");
    if (dryRunToggle) {
        dryRunToggle.addEventListener("change", async (e) => {
            const isDry = e.target.checked;
            try {
                const res = await fetch("/api/auth/toggle_dry_run", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ dry_run: isDry })
                });
                const data = await res.json();
                state.dryRun = data.dry_run;
                updateModeLabel();
                alert(data.message);
            } catch (err) {
                console.error("切换模式失败:", err);
            }
        });
    }

    // 9. 帐号模态弹窗开启与关闭
    const accountModal = document.getElementById("account-modal");
    document.getElementById("btn-open-account-modal")?.addEventListener("click", () => {
        accountModal?.classList.add("active");
    });
    document.getElementById("btn-close-account-modal")?.addEventListener("click", () => {
        accountModal?.classList.remove("active");
    });
    document.getElementById("btn-modal-close-footer")?.addEventListener("click", () => {
        accountModal?.classList.remove("active");
    });

    // 10. 浏览器登录唤起与会话同步
    document.getElementById("btn-launch-browser")?.addEventListener("click", async () => {
        try {
            const res = await fetch("/api/auth/login_browser", { method: "POST" });
            const data = await res.json();
            alert(data.message);
        } catch (err) {
            alert("唤起浏览器失败: " + err);
        }
    });

    document.getElementById("btn-sync-browser")?.addEventListener("click", async () => {
        try {
            const res = await fetch("/api/auth/sync_session", { method: "POST" });
            const data = await res.json();
            alert(data.message);
            loadAuthStatus();
        } catch (err) {
            alert("同步会话异常: " + err);
        }
    });

    // 11. 手动 Cookie 保存
    document.getElementById("btn-save-cookie-str")?.addEventListener("click", async () => {
        const area = document.getElementById("cookie-input-area");
        const cookieStr = area ? area.value.trim() : "";
        if (!cookieStr) {
            alert("请先粘贴 Cookie 字符串");
            return;
        }
        try {
            const res = await fetch("/api/auth/save_cookies", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ cookie_str: cookieStr })
            });
            const data = await res.json();
            alert(data.message);
            loadAuthStatus();
        } catch (err) {
            alert("保存 Cookie 失败: " + err);
        }
    });

    // 12. 帐号注销
    document.getElementById("btn-account-logout")?.addEventListener("click", async () => {
        if (!confirm("确定退出当前登录的 Mandarake 帐号吗？")) return;
        try {
            const res = await fetch("/api/auth/logout", { method: "POST" });
            const data = await res.json();
            alert(data.message);
            loadAuthStatus();
        } catch (err) {
            alert("注销失败: " + err);
        }
    });

    // 13. 下单确认模态框
    const orderModal = document.getElementById("order-confirm-modal");
    document.getElementById("btn-close-order-modal")?.addEventListener("click", () => {
        orderModal?.classList.remove("active");
    });
    document.getElementById("btn-cancel-order")?.addEventListener("click", () => {
        orderModal?.classList.remove("active");
    });

    document.getElementById("btn-do-order-submit")?.addEventListener("click", async () => {
        if (!state.pendingOrderItem) return;
        const item = state.pendingOrderItem;
        const submitBtn = document.getElementById("btn-do-order-submit");
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> 正在执行下单...`;
        }

        try {
            const res = await fetch("/api/order/buy", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    item_id: item.id,
                    title: item.title,
                    price: item.price,
                    img_url: item.img_url,
                    shop: item.shop,
                    dry_run: state.dryRun
                })
            });
            const data = await res.json();
            orderModal?.classList.remove("active");
            alert(data.message);

            // 实时将当前视图对应商品卡片调整为售罄置灰
            const cardEl = document.querySelector(`.product-card[data-item-id="${item.id}"]`);
            if (cardEl) {
                cardEl.classList.add("is-soldout");
                const badgeWrap = cardEl.querySelector(".card-badges");
                if (badgeWrap) {
                    badgeWrap.innerHTML = '<span class="badge-tag soldout">🔴 売り切れ</span>';
                }
                const buyBtn = cardEl.querySelector(".btn-oneclick-order");
                if (buyBtn) {
                    buyBtn.disabled = true;
                    buyBtn.title = "商品已售罄";
                }
            }

            loadOrders();
            loadProducts();
            loadStats();
        } catch (err) {
            alert("下单请求发生错误: " + err);
        } finally {
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerHTML = `<i class="fa-solid fa-bolt"></i> 立即执行下单`;
            }
        }
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

    // 16. 订单记录操作
    document.getElementById("btn-refresh-orders")?.addEventListener("click", loadOrders);
    document.getElementById("btn-clear-orders")?.addEventListener("click", async () => {
        if (confirm("确定清空全部下单历史记录吗？")) {
            await fetch("/api/orders/clear", { method: "POST" });
            loadOrders();
        }
    });

    // 17. 终端控制
    document.getElementById("btn-clear-terminal")?.addEventListener("click", async () => {
        await fetch("/api/terminal/clear", { method: "POST" });
        const container = document.getElementById("terminal-output-container");
        if (container) container.innerHTML = `<div class="log-line info">[SYS] 控制台已清屏</div>`;
    });

    // 18. 监控频率与重试参数保存
    document.getElementById("btn-save-schedule-cfg")?.addEventListener("click", async () => {
        const intervalSec = parseInt(document.getElementById("cfg-interval-seconds")?.value, 10) || 60;
        const retryCount = parseInt(document.getElementById("cfg-retry-count")?.value, 10) || 3;
        const retryDelay = parseFloat(document.getElementById("cfg-retry-delay")?.value) || 5;

        try {
            const res = await fetch("/api/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    schedule: {
                        interval_seconds: intervalSec,
                        retry_count: retryCount,
                        retry_delay: retryDelay
                    }
                })
            });
            const data = await res.json();
            alert("调度与重试参数已成功保存！当前扫描间隔: " + intervalSec + " 秒");
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

    // 21. 本地声音与桌面通知勾选自动保存
    const soundCheck = document.getElementById("cfg-sound-enabled");
    const deskCheck = document.getElementById("cfg-desktop-enabled");
    const saveLocalNotifiers = async () => {
        try {
            await fetch("/api/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    notifiers: {
                        sound: { enabled: soundCheck?.checked ?? true },
                        desktop: { enabled: deskCheck?.checked ?? true }
                    }
                })
            });
        } catch (e) {
            console.error("保存本地通知设置失败:", e);
        }
    };
    soundCheck?.addEventListener("change", saveLocalNotifiers);
    deskCheck?.addEventListener("change", saveLocalNotifiers);

    // 22. 监控调度顶部控制
    document.getElementById("btn-monitor-check-now")?.addEventListener("click", async () => {
        const btn = document.getElementById("btn-monitor-check-now");
        if (btn) btn.disabled = true;
        try {
            const res = await fetch("/api/monitor/check_now", { method: "POST" });
            const data = await res.json();
            alert(`巡检完成！发现并命中 ${data.matches_count || 0} 件新上架目标商品！`);
            loadStats();
            loadProducts();
        } catch (err) {
            alert("执行巡检失败: " + err);
        } finally {
            if (btn) btn.disabled = false;
        }
    });

    document.getElementById("btn-monitor-burst")?.addEventListener("click", async () => {
        try {
            const res = await fetch("/api/monitor/trigger_burst", { method: "POST" });
            const data = await res.json();
            alert(data.message);
            loadStats();
        } catch (err) {
            alert("启动高频爆发模式失败: " + err);
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
        const id = document.getElementById("modal-rule-id")?.value || "";
        const name = document.getElementById("modal-rule-name")?.value.trim() || "";
        const catCode = document.getElementById("modal-rule-cat")?.value || "020114";
        const kwStr = document.getElementById("modal-rule-keywords")?.value.trim() || "";
        const exStr = document.getElementById("modal-rule-exclude")?.value.trim() || "";
        const maxPrice = parseInt(document.getElementById("modal-rule-max-price")?.value, 10) || 0;
        const autoOrder = document.getElementById("modal-rule-auto-order")?.checked || false;

        const activeModeBtn = document.querySelector("#modal-rule-mode-group .seg-btn.active");
        const matchMode = activeModeBtn ? activeModeBtn.getAttribute("data-mode") : "and";

        if (!name) {
            alert("请填写规则名称");
            return;
        }

        const keywords = kwStr ? kwStr.split(/\s+/).filter(Boolean) : [];
        const excludeKeywords = exStr ? exStr.split(/\s+/).filter(Boolean) : [];

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
                    auto_order: autoOrder,
                    enabled: true
                })
            });
            const data = await res.json();
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

// 创建单个商品卡片 DOM
function createProductCard(item) {
    const card = document.createElement("div");
    const isInStock = item.is_in_stock === 1;
    const isNew = item.is_new === 1;
    
    card.className = "product-card" + (isInStock ? "" : " is-soldout");
    card.setAttribute("data-item-id", item.id);

    // 缩略图 fallback
    const imgUrl = item.img_url || "https://order.mandarake.co.jp/content/img/icon/shortcut/192.png";
    const priceText = item.price ? `${item.price.toLocaleString()}円` : "价格未标明";
    const shopText = item.shop || "まんだらけ";

    card.innerHTML = `
        <div class="card-media-wrap">
            <img src="${imgUrl}" alt="${escapeHtml(item.title)}" loading="lazy" onerror="this.src='https://order.mandarake.co.jp/content/img/icon/shortcut/192.png'">
            <div class="card-badges">
                ${isInStock ? '<span class="badge-tag stock">🟢 在库有货</span>' : '<span class="badge-tag soldout">🔴 売り切れ</span>'}
                ${isNew ? '<span class="badge-tag new-arrival">⚡ 新着</span>' : ''}
            </div>
            <div class="shop-tag"><i class="fa-solid fa-store"></i> ${escapeHtml(shopText)}</div>
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
            <div class="card-actions">
                <button class="btn-buy-now btn-oneclick-order" ${!isInStock ? 'disabled title="商品已售罄"' : ''}>
                    <i class="fa-solid fa-bolt"></i> ${state.dryRun ? '仿真测试下单' : '一键立即下单'}
                </button>
                <a href="${item.url}" target="_blank" rel="noopener" class="btn-item-link" title="在 Mandarake 官网打开">
                    <i class="fa-solid fa-arrow-up-right-from-square"></i> 官网
                </a>
            </div>
        </div>
    `;

    // 绑定下单按钮事件
    const buyBtn = card.querySelector(".btn-oneclick-order");
    if (buyBtn && isInStock) {
        buyBtn.addEventListener("click", () => {
            promptOrderConfirm(item);
        });
    }

    return card;
}

// 弹出下单确认弹窗
function promptOrderConfirm(item) {
    state.pendingOrderItem = item;
    const modal = document.getElementById("order-confirm-modal");
    if (!modal) return;

    document.getElementById("order-item-img").src = item.img_url || "";
    document.getElementById("order-item-title").textContent = item.title;
    document.getElementById("order-item-price").textContent = item.price ? `${item.price.toLocaleString()}円` : "";
    document.getElementById("order-item-shop").textContent = item.shop || "Mandarake";
    document.getElementById("order-item-id").textContent = `商品番号: ${item.item_no || item.id} | ID: ${item.id}`;

    const warningEl = document.getElementById("order-mode-warning");
    if (warningEl) {
        if (state.dryRun) {
            warningEl.className = "order-warning";
            warningEl.innerHTML = `
                <i class="fa-solid fa-shield-halved"></i>
                <span>当前处于 <strong>Dry-Run 仿真测试模式</strong>，将模拟加购并结算至最终确认页，在提交前自动拦截并清空测试购物车，<strong>安全演练不产生真实扣款</strong>。</span>
            `;
        } else {
            warningEl.className = "order-warning danger";
            warningEl.style.borderColor = "var(--danger)";
            warningEl.style.background = "var(--danger-bg)";
            warningEl.style.color = "#fca5a5";
            warningEl.innerHTML = `
                <i class="fa-solid fa-triangle-exclamation"></i>
                <span>⚠️ 当前处于 <strong>【实盘下单模式】</strong>！点击确认将向 Mandarake 官网提交真实订单，扣除账户或生成真实待付款订单！请谨慎操作！</span>
            `;
        }
    }

    modal.classList.add("active");
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

        // 帐号状态同步
        if (data.auth) {
            state.isLoggedIn = data.auth.is_logged_in;
            state.accountName = data.auth.account_name;
            state.dryRun = data.auth.dry_run;
            updateAuthUI();
            updateModeLabel();
        }
    } catch (err) {
        console.debug("获取统计失败:", err);
    }
}

// --- 帐号与登录态管理 ---
async function loadAuthStatus() {
    try {
        const res = await fetch("/api/auth/status");
        const data = await res.json();
        if (!data.success) return;

        state.isLoggedIn = data.is_logged_in;
        state.accountName = data.account_name;
        state.dryRun = data.dry_run;

        updateAuthUI();
        updateModeLabel();
    } catch (err) {
        console.error("加载登录状态失败:", err);
    }
}

function updateAuthUI() {
    const pill = document.getElementById("auth-status-pill");
    const text = document.getElementById("auth-text");
    const dot = pill ? pill.querySelector(".status-dot") : null;
    const modalTitle = document.getElementById("modal-account-title");
    const modalDesc = document.getElementById("modal-account-desc");
    const modalIcon = document.getElementById("modal-status-icon");

    if (state.isLoggedIn) {
        if (dot) {
            dot.className = "status-dot connected";
        }
        const name = state.accountName || "已登录用户";
        if (text) text.textContent = name;
        if (modalTitle) modalTitle.textContent = `已连接: ${name}`;
        if (modalDesc) modalDesc.textContent = "当前 Mandarake 会话有效，支持自动加购与一键下单抢购";
        if (modalIcon) modalIcon.innerHTML = `<i class="fa-solid fa-circle-check" style="color:var(--success)"></i>`;
    } else {
        if (dot) {
            dot.className = "status-dot disconnected";
        }
        if (text) text.textContent = "未登录帐号";
        if (modalTitle) modalTitle.textContent = "未登录帐号";
        if (modalDesc) modalDesc.textContent = "登录后即可启用一键快速下单与定点自动抢购功能";
        if (modalIcon) modalIcon.innerHTML = `<i class="fa-solid fa-user-shield" style="color:var(--brand-primary)"></i>`;
    }
}

function updateModeLabel() {
    const lbl = document.getElementById("mode-label-text");
    const toggle = document.getElementById("dry-run-toggle");
    const cfgCheck = document.getElementById("cfg-dry-run-check");

    if (toggle) toggle.checked = state.dryRun;
    if (cfgCheck) cfgCheck.checked = state.dryRun;

    if (lbl) {
        if (state.dryRun) {
            lbl.textContent = "🛡️ 仿真测试 (Dry-Run)";
            lbl.style.color = "var(--text-primary)";
        } else {
            lbl.textContent = "⚠️ 实盘下单模式";
            lbl.style.color = "var(--danger)";
        }
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

// --- 订单历史记录 ---
async function loadOrders() {
    try {
        const res = await fetch("/api/orders?limit=100");
        const data = await res.json();
        if (!data.success) return;

        const countBadge = document.getElementById("orders-count-badge");
        if (countBadge) countBadge.textContent = data.items.length;

        const tbody = document.getElementById("tbody-orders");
        if (!tbody) return;

        if (data.items.length === 0) {
            tbody.innerHTML = `<tr><td colspan="8" class="text-center" style="padding:40px;color:var(--text-muted)">暂无下单与购买记录</td></tr>`;
            return;
        }

        tbody.innerHTML = "";
        data.items.forEach(ord => {
            const tr = document.createElement("tr");
            const timeStr = new Date(ord.created_at * 1000).toLocaleString("zh-CN", { hour12: false });
            const statusClass = ord.status || "PENDING";
            const priceStr = ord.price ? `${ord.price.toLocaleString()}円` : "-";

            tr.innerHTML = `
                <td><small style="color:var(--text-muted)">${timeStr}</small></td>
                <td>
                    <div style="display:flex;align-items:center;gap:8px;">
                        ${ord.img_url ? `<img src="${ord.img_url}" style="width:36px;height:36px;object-fit:cover;border-radius:4px;">` : ''}
                        <div>
                            <div style="font-weight:600;font-size:12px;color:#fff;">${escapeHtml(ord.title || ord.item_id)}</div>
                            <small style="color:var(--text-muted)">ID: ${escapeHtml(ord.item_id)}</small>
                        </div>
                    </div>
                </td>
                <td><strong style="color:var(--brand-primary)">${priceStr}</strong></td>
                <td>${escapeHtml(ord.shop || 'Mandarake')}</td>
                <td>
                    <span style="font-size:12px;color:var(--text-primary);font-weight:600;">
                        ${ord.trigger_type === 'auto' ? '🤖 自动抢购' : '👆 手动一键'}
                    </span>
                </td>
                <td>
                    <span class="badge-order-status ${ord.status === 'FAILED' ? 'FAILED' : (ord.dry_run ? 'DRY_RUN' : 'SUCCESS')}">
                        ${ord.dry_run ? '模拟模式' : (ord.status === 'SUCCESS' ? '真实模式 (成功)' : (ord.status === 'FAILED' ? '真实模式 (失败)' : '真实模式'))}
                    </span>
                </td>
                <td>
                    <div style="font-size:11px;color:var(--text-secondary);max-width:320px;">
                        ${escapeHtml(ord.message || '-')}
                    </div>
                    <small style="color:var(--text-muted)">耗时: ${ord.duration_ms}ms</small>
                </td>
                <td>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteOrderRecord('${ord.id}')">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                </td>
            `;
            tbody.appendChild(tr);
        });

    } catch (err) {
        console.error("加载订单历史异常:", err);
    }
}

async function deleteOrderRecord(orderId) {
    if (!confirm("确定删除此条订单记录吗？")) return;
    try {
        await fetch("/api/orders/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ order_id: orderId })
        });
        loadOrders();
    } catch (err) {
        alert("删除记录失败: " + err);
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
            }
        } catch (err) {
            // ignore heartbeat
        }
    };

    evtSource.onerror = (err) => {
        console.debug("SSE 连接中断，正在自动重连...", err);
    };
}

function appendTerminalLine(log) {
    const termBody = document.getElementById("terminal-output-container");
    const autoScrollCheck = document.getElementById("terminal-autoscroll");
    if (!termBody) return;

    // 检查等级过滤
    const filterSelect = document.getElementById("terminal-level-select");
    const curLevel = filterSelect ? filterSelect.value : "ALL";
    if (curLevel !== "ALL" && log.level !== curLevel) {
        return;
    }

    const line = document.createElement("div");
    line.className = `log-line ${(log.level || 'info').toLowerCase()}`;
    line.textContent = log.formatted || `[${log.time_short}] [${log.level}] ${log.message}`;
    termBody.appendChild(line);

    // 限制最大行数
    if (termBody.children.length > 500) {
        termBody.removeChild(termBody.firstElementChild);
    }

    if (autoScrollCheck && autoScrollCheck.checked) {
        termBody.scrollTop = termBody.scrollHeight;
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
        const sound = notifiers.sound || {};
        const desktop = notifiers.desktop || {};

        // 1. 调度与重试参数
        const intervalInput = document.getElementById("cfg-interval-seconds");
        if (intervalInput) intervalInput.value = sched.interval_seconds || 60;
        const retryCountInput = document.getElementById("cfg-retry-count");
        if (retryCountInput) retryCountInput.value = sched.retry_count !== undefined ? sched.retry_count : 3;
        const retryDelayInput = document.getElementById("cfg-retry-delay");
        if (retryDelayInput) retryDelayInput.value = sched.retry_delay !== undefined ? sched.retry_delay : 5;

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

        // 4. 声音与桌面
        const soundCheck = document.getElementById("cfg-sound-enabled");
        if (soundCheck) soundCheck.checked = sound.enabled !== false;
        const deskCheck = document.getElementById("cfg-desktop-enabled");
        if (deskCheck) deskCheck.checked = desktop.enabled !== false;

        // 5. 规则列表
        renderRulesTable(cfg.rules || []);

    } catch (err) {
        console.error("加载监控配置失败:", err);
    }
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

        // 关键词渲染 (含 AND / OR 模式标识)
        let kwHtml = "-";
        if (rule.keywords && rule.keywords.length > 0) {
            const mode = (rule.match_mode || "and").toUpperCase();
            kwHtml = `<span class="badge-mode">${mode}</span>` + 
                rule.keywords.map(k => `<span class="kw-pill">${escapeHtml(k)}</span>`).join("");
        }

        // 排除词渲染
        let exHtml = "-";
        if (rule.exclude_keywords && rule.exclude_keywords.length > 0) {
            exHtml = rule.exclude_keywords.map(e => `<span class="ex-kw-pill">-${escapeHtml(e)}</span>`).join("");
        }

        const maxPriceStr = rule.max_price > 0 ? `≤ ${rule.max_price.toLocaleString()}円` : "不限";
        const autoOrderBadge = rule.auto_order 
            ? `<span class="badge-tag stock" title="发现即触发自动下单">⚡ 已关联</span>` 
            : `<span style="color:var(--text-muted)">否</span>`;

        const ruleId = rule.id || `rule_${idx}`;
        const ruleName = rule.name || "";
        const isEnabled = rule.enabled !== false;

        tr.innerHTML = `
            <td><strong>${escapeHtml(ruleName)}</strong></td>
            <td><code>${escapeHtml(rule.category_code || '020114')}</code></td>
            <td>${kwHtml}</td>
            <td>${exHtml}</td>
            <td><strong style="color:var(--warning)">${maxPriceStr}</strong></td>
            <td>${autoOrderBadge}</td>
            <td>
                <label class="switch-toggle" title="启用/暂停此规则">
                    <input type="checkbox" class="rule-status-toggle" ${isEnabled ? 'checked' : ''}>
                    <span class="slider"></span>
                </label>
            </td>
            <td>
                <button class="btn btn-sm btn-outline-danger btn-delete-rule" title="删除规则">
                    <i class="fa-solid fa-trash-can"></i>
                </button>
            </td>
        `;

        const statusToggle = tr.querySelector(".rule-status-toggle");
        if (statusToggle) {
            statusToggle.addEventListener("change", () => {
                toggleRuleStatus(ruleId, ruleName, statusToggle.checked);
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

    document.getElementById("modal-rule-id").value = rule ? (rule.id || "") : "";
    document.getElementById("modal-rule-name").value = rule ? (rule.name || "") : "";
    document.getElementById("modal-rule-cat").value = rule ? (rule.category_code || "020114") : "020114";
    document.getElementById("modal-rule-keywords").value = rule && rule.keywords ? rule.keywords.join(" ") : "";
    document.getElementById("modal-rule-exclude").value = rule && rule.exclude_keywords ? rule.exclude_keywords.join(" ") : "難あり ジャンク";
    document.getElementById("modal-rule-max-price").value = rule && rule.max_price ? rule.max_price : "";
    document.getElementById("modal-rule-auto-order").checked = rule ? Boolean(rule.auto_order) : false;

    // 模式切换
    const mode = rule ? (rule.match_mode || "and") : "and";
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

    if (sched.running) {
        if (dot) dot.className = "status-indicator-dot";
        if (title) {
            title.textContent = sched.in_burst_mode 
                ? `⚡ 高频爆发抢购模式运行中 (剩余 ${sched.burst_remaining_seconds}s)`
                : `监控调度器运行中 (每 ${sched.interval_seconds} 秒巡检一次)`;
        }
    } else {
        if (dot) dot.className = "status-indicator-dot stopped";
        if (title) title.textContent = "监控调度器已停止";
    }

    if (meta) {
        const lastCheckStr = sched.last_check_time > 0 
            ? new Date(sched.last_check_time * 1000).toLocaleTimeString("zh-CN", { hour12: false })
            : "从未";
        meta.textContent = `上次检测: ${lastCheckStr} | 累计轮次: ${sched.total_checks || 0} | 累计命中: ${sched.total_matches || 0}`;
    }
}

// --- 常用预设关键词管理与筛选 ---

function initPresetKeywords() {
    try {
        const saved = localStorage.getItem(PRESET_STORAGE_KEY);
        if (saved) {
            const parsed = JSON.parse(saved);
            if (Array.isArray(parsed) && parsed.length > 0) {
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

    // 恢复默认预设
    document.getElementById("btn-reset-presets")?.addEventListener("click", () => {
        if (confirm("确定要恢复默认预设标签列表吗？")) {
            state.presetKeywords = [...DEFAULT_PRESET_KEYWORDS];
            savePresetKeywords();
            renderPresetKeywords();
        }
    });
}
