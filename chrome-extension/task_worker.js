// Runs scheduled jobs in a tab of the user's current Chrome profile.
importScripts("search_cards.js");
importScripts("seller_credit.js");

let polling = false;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function bridgeConfig() {
  const response = await fetch(chrome.runtime.getURL("bridge-config.json"));
  if (!response.ok) throw new Error("缺少本机配对配置");
  const config = await response.json();
  const endpoint = new URL(config.endpoint);
  if (endpoint.protocol !== "http:" || endpoint.hostname !== "127.0.0.1" || !config.token) {
    throw new Error("本机配对配置无效");
  }
  return { base: new URL("/api/chrome-bridge/", endpoint).href, token: config.token };
}

async function bridgeRequest(config, path, method = "GET", body) {
  const response = await fetch(new URL(path, config.base), {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Chrome-Bridge-Token": config.token,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || `本机项目返回 ${response.status}`);
  return result;
}

async function waitForCards(tabId) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    try {
      const [capture] = await chrome.scripting.executeScript({
        target: { tabId }, func: collectSearchCards, args: [50],
      });
      if (capture?.result?.cards?.length) return capture.result;
    } catch (_) {
      // The tab may still be loading.
    }
    await pause(750);
  }
  throw new Error("现有 Chrome 的闲鱼搜索结果未加载成功");
}

function applySearchFilters(options) {
  const setCheckbox = (label, enabled) => {
    const item = [...document.querySelectorAll('[class*="search-checkbox-item-container"]')]
      .find((element) => element.querySelector('[class*="search-checkbox-label"]')?.textContent?.trim() === label);
    if (!item) throw new Error(`未找到筛选条件：${label}`);
    const checkbox = item.querySelector('[class*="search-checkbox--"]');
    if (!checkbox) throw new Error(`筛选条件结构已变化：${label}`);
    const checked = checkbox.className.includes("search-checkbox-checked");
    if (checked !== enabled) item.click();
  };
  setCheckbox("个人闲置", options.personal_only);
  setCheckbox("包邮", options.free_shipping);
  return true;
}

function goToSearchPage(pageNumber) {
  const page = [...document.querySelectorAll('[class*="search-pagination-page-box"]')]
    .find((element) => element.textContent?.trim() === String(pageNumber));
  if (!page) throw new Error(`未找到第 ${pageNumber} 页入口`);
  page.click();
  return true;
}

function isSearchPageActive(pageNumber) {
  return [...document.querySelectorAll('[class*="search-pagination-page-box-active"]')]
    .some((element) => element.textContent?.trim() === String(pageNumber));
}

async function waitForPage(tabId, pageNumber) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId }, func: isSearchPageActive, args: [pageNumber],
    });
    if (result?.result) return await waitForCards(tabId);
    await pause(750);
  }
  throw new Error(`闲鱼搜索第 ${pageNumber} 页未加载成功`);
}

async function executeChromeJob(config, job) {
  const searchUrl = `https://www.goofish.com/search?q=${encodeURIComponent(job.keyword)}`;
  const tab = await chrome.tabs.create({ url: searchUrl, active: false });
  try {
    await waitForCards(tab.id);
    await chrome.scripting.executeScript({
      target: { tabId: tab.id }, func: applySearchFilters,
      args: [{ personal_only: job.personal_only, free_shipping: job.free_shipping }],
    });
    await pause(1200);
    for (let pageNumber = 1; pageNumber <= job.max_pages; pageNumber += 1) {
      const capture = await waitForPage(tab.id, pageNumber);
      if (job.decision_mode !== "keyword") await enrichSellerCredit(capture.cards);
      await bridgeRequest(config, "import", "POST", {
        job_id: job.job_id,
        task_name: job.task_name,
        ...capture,
      });
      if (pageNumber < job.max_pages) {
        await chrome.scripting.executeScript({
          target: { tabId: tab.id }, func: goToSearchPage, args: [pageNumber + 1],
        });
        await pause(1300);
      }
    }
  } finally {
    await chrome.tabs.remove(tab.id).catch(() => {});
  }
}

async function pollChromeTasks() {
  if (polling) return;
  if (!(await chrome.permissions.contains({ origins: ["http://127.0.0.1/*"] }))) return;
  polling = true;
  try {
    const config = await bridgeConfig();
    const { job } = await bridgeRequest(config, "next");
    if (!job) return;
    let error = null;
    try {
      await executeChromeJob(config, job);
    } catch (exc) {
      error = String(exc?.message || exc);
      console.error("现有 Chrome 任务失败", error);
    }
    await bridgeRequest(config, "complete", "POST", { job_id: job.job_id, error });
  } catch (exc) {
    console.warn("本机 Chrome 任务轮询失败", exc?.message || exc);
  } finally {
    polling = false;
  }
}

chrome.runtime.onInstalled.addListener(() => chrome.alarms.create("chrome-tasks", { periodInMinutes: 1 }));
chrome.runtime.onStartup.addListener(() => chrome.alarms.create("chrome-tasks", { periodInMinutes: 1 }));
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "chrome-tasks") pollChromeTasks();
});
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "pollChromeTasks") {
    pollChromeTasks();
    sendResponse({ ok: true });
  }
});
chrome.alarms.create("chrome-tasks", { periodInMinutes: 1 });
