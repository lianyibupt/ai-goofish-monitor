// Serialized by chrome.scripting; read only the current item's seller header.
function collectItemSeller() {
  if (location.origin !== "https://www.goofish.com" || location.pathname !== "/item") return null;
  const header = document.querySelector('[class*="item-user-info-container"]');
  if (!header) return null;
  // These exact platform image assets were checked against the visible badges.
  const creditImages = {
    "O1CN018Uwwvn24tF1X9eUV0_!!6000000007448-2-tps-228-48.png": "卖家信用优秀",
    "O1CN01YTUhW41aSHETi0kIJ_!!6000000003328-2-tps-228-48.png": "卖家信用极好",
  };
  let credit = "";
  let source = "";
  for (const node of header.querySelectorAll('[class*="item-user-info-level"] img, [class*="item-user-info-level"] [title]')) {
    const label = node.getAttribute("title") || node.getAttribute("alt") || node.textContent?.trim() || "";
    if (/^卖家信用(?:极好|优秀|良好|一般|较差|差)$/.test(label)) {
      credit = label; source = "闲鱼商品详情页信用文字"; break;
    }
    if (node.src) {
      const url = new URL(node.src, location.href);
      const filename = url.pathname.split("/").pop();
      if ((url.hostname === "alicdn.com" || url.hostname.endsWith(".alicdn.com")) && creditImages[filename]) {
        credit = creditImages[filename]; source = "闲鱼商品详情页信用图片"; break;
      }
    }
  }
  const labels = [...header.querySelectorAll('[class*="item-user-info-label"]')].map(node => node.textContent?.trim() || "");
  return {
    item_id: new URL(location.href).searchParams.get("id"),
    seller: header.querySelector('[class*="item-user-info-nick"]')?.textContent?.trim() || "",
    seller_credit: credit,
    seller_credit_source: source,
    seller_registration: labels.find(text => text.startsWith("来闲鱼")) || "未知",
    seller_sold_count: labels.find(text => /^卖出\d+件宝贝$/.test(text)) || "",
    seller_positive_rate: labels.find(text => text.startsWith("好评率")) || "",
    seller_profile_url: header.closest('a[href*="/personal?"]')?.href || "",
  };
}

async function enrichSellerCredit(cards) {
  const missing = cards.filter(card => !card.seller_credit);
  if (!missing.length) return cards;
  const tab = await chrome.tabs.create({url: "about:blank", active: false});
  try {
    for (const card of missing) {
      const expectedId = new URL(card.url).searchParams.get("id");
      await chrome.tabs.update(tab.id, {url: card.url});
      let found = false;
      for (let attempt = 0; attempt < 12; attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 500));
        try {
          const [capture] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: collectItemSeller});
          const seller = capture?.result;
          if (seller?.item_id === expectedId && seller.seller) {
            Object.assign(card, seller);
            found = true;
            break;
          }
        } catch (_) { /* The tab may still be navigating. */ }
      }
      if (!found) console.warn("卖家信用未采集，保留待核实", expectedId);
    }
  } finally {
    await chrome.tabs.remove(tab.id).catch(() => {});
  }
  return cards;
}
