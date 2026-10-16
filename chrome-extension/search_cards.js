// This function is serialized by chrome.scripting and runs in the existing Goofish tab.
function collectSearchCards(maxCards) {
  if (location.origin !== "https://www.goofish.com" || location.pathname !== "/search") {
    throw new Error("请先在当前 Chrome 打开闲鱼搜索结果页");
  }
  const links = [...document.querySelectorAll('a[href*="/item?id="]')].slice(0, maxCards);
  if (!links.length) {
    throw new Error("当前页面没有加载出商品；请等待搜索结果出现后重试");
  }
  const cards = links.map((link) => {
    const titleNode = link.querySelector('[class*="row1-wrap-title"]');
    const priceNode = link.querySelector('[class*="price-wrap"]');
    const imageNode = link.querySelector('img[class*="feeds-image"]');
    const areaNode = link.querySelector('[class*="seller-text-wrap"]');
    const publishNode = link.querySelector('[title*="发布"]');
    // Only trust the platform's credit badge, never text in the product description.
    const sellerCredit = [...link.querySelectorAll('[class*="credit-container"] [title]')]
      .map((node) => node.getAttribute("title")?.trim() || "")
      .find((text) => /^卖家信用(?:极好|优秀|良好|一般|较差|差)$/.test(text)) || "";
    return {
      url: link.href,
      title: titleNode?.getAttribute("title") || titleNode?.innerText || "",
      price: priceNode?.innerText?.replace(/\s+/g, " ").trim() || "",
      area: areaNode?.getAttribute("title") || "",
      image_url: imageNode?.src || "",
      publish_time: publishNode?.getAttribute("title") || "",
      seller_credit: sellerCredit,
    };
  });
  return { page_url: location.href, cards };
}
