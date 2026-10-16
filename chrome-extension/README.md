# Xianyu Login State Extractor Chrome Extension

This Chrome extension helps extract complete login state information from Xianyu (Goofish) for use with the monitoring robot. It also records browser environment hints and request headers to better mimic a real session.

## Installation

1. Open Chrome and navigate to `chrome://extensions`
2. Enable "Developer mode" in the top right corner
3. Click "Load unpacked" and select the `chrome-extension` directory
4. The extension icon should now appear in your toolbar

## Usage

1. Navigate to [https://www.goofish.com](https://www.goofish.com)
2. Log in to your account
3. Click the extension icon in the toolbar
4. Click "Extract Login State" (collects cookies + environment + headers，自动过滤无用/超大字段)
5. The complete JSON will be displayed - click "Copy to Clipboard"
6. Save the JSON文本到 `xianyu_state.json`（或自定义文件名）即可

## 在当前 Chrome 中运行一次监控

这条路径直接读取你正在使用的 Chrome 标签页，不会启动 Playwright 浏览器。

1. 在项目目录启动本地服务；启动时会生成被 Git 忽略的 `chrome-extension/bridge-config.json` 配对文件。将 `.env` 中的 `CHROME_BROWSER_MODE` 设为 `existing`，任务启动与定时调度就会投递给当前 Chrome 扩展。
2. 在 Chrome 中加载本仓库的 `chrome-extension/` 目录。如果已经加载过此本地扩展，更新代码后需要在 Chrome 中手动重新加载。
3. 用同一个 Chrome 打开与已启用任务关键词一致的闲鱼搜索结果页，等待商品卡片显示。
4. 打开扩展，点击“启用现有 Chrome 的定时任务”，首次运行时 Chrome 会询问是否允许扩展访问本机 `127.0.0.1`。此后调度器投递的任务会由这个 Chrome 处理。也可以填写任务名称和本次最多分析的商品数，点击“从当前闲鱼标签页采集并导入本机项目”立即采集当前页。
5. 扩展会显示读取、符合价格及去重条件、保存的数量；结果也会出现在项目结果页。

本地接口使用随机配对令牌，仅接收与任务关键词相符的闲鱼搜索页和商品链接。令牌只保存在被忽略的本地配置文件中，不要提交或分享。手动采集读取当前搜索页的可见卡片；定时任务在现有 Chrome 中打开搜索标签，按任务的个人闲置、包邮和页数条件采集。服务端应用价格范围与已有结果去重。地区和发布时间筛选在该模式下暂不支持，含这些条件的任务会拒绝启动。现有 Chrome 必须保持运行且扩展已启用；每分钟轮询一次待执行任务。

定时任务上传每批商品后，服务端立即确认接收，再在后台按批次分析和保存。Chrome 页面采集结束后，任务仍会保持运行状态，直到后台分析结束。任务日志会记录扩展接取、批次接收、分析及保存数量；重复点击启动会返回“已在运行中”，不会重复执行。点击停止会取消该任务尚未完成的分析，已保存的结果会保留。

1.3 版会读取搜索卡片中平台的“卖家信用”徽标；缺失时在当前 Chrome 中逐条打开详情页补采，结束后关闭临时标签。详情页同时识别信用文字和已核验的“优秀／极好”信用图片，不使用商品描述、“百分百好评”或未知图片推断等级。信用独立传给 AI，当前任务规则接受“卖家信用优秀”和“卖家信用极好”；仍未采集到时标记待核实。补采到历史商品缺失的信用后，会重新分析并更新原记录，保留屏蔽状态，不重复发送历史推荐通知。更新采集器后需手动重新加载扩展。

## Features

- Extracts all cookies including HttpOnly cookies
- Captures browser environment (UA, locale, timezone, screen size, device memory, hardware concurrency)
- Captures observed request headers for the current tab
- Captures localStorage/sessionStorage snapshot for the current domain（会自动丢弃超大或无用字段）
- Outputs a single JSON payload, ready for the monitoring robot
- Copy to clipboard with real-time status feedback

## How It Works

The extension uses the `chrome.cookies` API to access all cookies for the `.goofish.com` domain, including those with the HttpOnly flag set. This bypasses the normal JavaScript security restrictions that prevent access to these cookies.
