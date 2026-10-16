document.getElementById("enableScheduledBtn").addEventListener("click", async () => {
  const status = document.getElementById("scheduledStatus");
  try {
    const allowed = await chrome.permissions.request({ origins: ["http://127.0.0.1/*"] });
    if (!allowed) throw new Error("未获得访问本机项目的授权");
    await chrome.runtime.sendMessage({ type: "pollChromeTasks" });
    status.textContent = "已启用：本机服务投递的任务会在这个 Chrome 中运行。";
  } catch (error) {
    status.textContent = `启用失败：${error.message}`;
  }
});

document.getElementById("runLocalBtn").addEventListener("click", async () => {
  const button = document.getElementById("runLocalBtn");
  const status = document.getElementById("runLocalStatus");
  const taskName = document.getElementById("taskName").value.trim();
  const maxCards = Number(document.getElementById("cardLimit").value);
  if (!taskName || !Number.isInteger(maxCards) || maxCards < 1 || maxCards > 50) {
    status.textContent = "请输入任务名称和 1 至 50 的商品数量。";
    return;
  }
  button.disabled = true;
  status.textContent = "正在读取当前 Chrome 标签页…";
  try {
    // Chrome requires this request to originate from the user's button click.
    const allowed = await chrome.permissions.request({ origins: ["http://127.0.0.1/*"] });
    if (!allowed) throw new Error("未获得访问本机项目的授权");
    chrome.runtime.sendMessage({ type: "pollChromeTasks" }).catch(() => {});
    const configResponse = await fetch(chrome.runtime.getURL("bridge-config.json"));
    if (!configResponse.ok) throw new Error("缺少本机配对配置；请先启动项目服务");
    const config = await configResponse.json();
    const endpoint = new URL(config.endpoint);
    if (endpoint.protocol !== "http:" || endpoint.hostname !== "127.0.0.1" || !config.token) {
      throw new Error("本机配对配置无效");
    }
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !tab.url?.startsWith("https://www.goofish.com/search?")) {
      throw new Error("当前标签页不是闲鱼搜索结果页");
    }
    const [capture] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: collectSearchCards,
      args: [maxCards],
    });
    if (!capture?.result) throw new Error("读取商品卡片失败");
    status.textContent = "正在补采缺失的卖家信用…";
    await enrichSellerCredit(capture.result.cards);
    status.textContent = "正在本机分析并保存商品…";
    const response = await fetch(endpoint.href, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Chrome-Bridge-Token": config.token,
      },
      body: JSON.stringify({ task_name: taskName, ...capture.result }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || `本机项目返回 ${response.status}`);
    status.textContent = `完成：读取 ${result.received} 条，符合价格及去重条件 ${result.selected} 条，保存 ${result.saved} 条。`;
  } catch (error) {
    status.textContent = `运行失败：${error.message}`;
  } finally {
    button.disabled = false;
  }
});
