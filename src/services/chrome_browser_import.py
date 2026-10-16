"""Import search cards read from the user's existing Chrome tab."""

import hmac
import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from src.domain.models.task import Task


BRIDGE_CONFIG_PATH = Path(__file__).resolve().parents[2] / "chrome-extension" / "bridge-config.json"


def ensure_bridge_config(path: Path = BRIDGE_CONFIG_PATH) -> dict:
    """Keep a private pairing token shared by the local server and unpacked extension."""
    port = int(os.getenv("SERVER_PORT", "8000"))
    endpoint = f"http://127.0.0.1:{port}/api/chrome-bridge/import"
    path.parent.mkdir(parents=True, exist_ok=True)
    token = ""
    if path.exists():
        try:
            token = str(json.loads(path.read_text(encoding="utf-8")).get("token") or "")
        except (OSError, ValueError, TypeError):
            token = ""
    if len(token) < 32:
        token = secrets.token_urlsafe(32)
    config = {"endpoint": endpoint, "token": token}
    temporary = path.with_suffix(".json.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(config, handle)
    os.replace(temporary, path)
    os.chmod(path, 0o600)
    return config


def valid_bridge_token(provided: str, path: Path = BRIDGE_CONFIG_PATH) -> bool:
    if not provided:
        return False
    try:
        expected = str(json.loads(path.read_text(encoding="utf-8"))["token"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return hmac.compare_digest(provided, expected)


def _is_goofish_url(value: str, path: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "www.goofish.com"
        and parsed.path == path
    )


def _image_url(value: str) -> str:
    if value.startswith("//"):
        value = f"https:{value}"
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        return ""
    if parsed.hostname != "alicdn.com" and not parsed.hostname.endswith(".alicdn.com"):
        return ""
    return value


def normalize_search_cards(
    *, page_url: str, expected_keyword: str, cards: list[dict]
) -> list[dict]:
    """Validate provenance and map visible cards to the existing result schema."""
    if not _is_goofish_url(page_url, "/search"):
        raise ValueError("采集来源必须是闲鱼搜索页")
    query = parse_qs(urlsplit(page_url).query).get("q", [""])[0]
    if query.strip().casefold() != expected_keyword.strip().casefold():
        raise ValueError("搜索词与任务配置不一致")
    if not isinstance(cards, list) or not 1 <= len(cards) <= 50:
        raise ValueError("每次仅接受 1 至 50 条商品卡片")

    normalized = []
    seen_ids = set()
    for card in cards:
        if not isinstance(card, dict):
            raise ValueError("商品卡片格式错误")
        link = str(card.get("url") or "")
        if not _is_goofish_url(link, "/item"):
            raise ValueError("商品链接必须属于闲鱼")
        item_id = parse_qs(urlsplit(link).query).get("id", [""])[0]
        if not item_id.isdigit():
            raise ValueError("商品链接缺少有效 ID")
        title = str(card.get("title") or "").strip()
        price = str(card.get("price") or "").strip()
        if not title or not price or len(title) > 1000 or len(price) > 80:
            raise ValueError("商品标题或价格无效")
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        image = _image_url(str(card.get("image_url") or ""))
        credit = str(card.get("seller_credit") or "").strip()
        if credit not in {"卖家信用极好", "卖家信用优秀", "卖家信用良好", "卖家信用一般", "卖家信用较差", "卖家信用差"}:
            credit = "未知"
        normalized.append(
            {
                "商品标题": title,
                "当前售价": price,
                "商品原价": "暂无",
                "“想要”人数": str(card.get("want_count") or "NaN")[:30],
                "商品标签": [],
                "发货地区": str(card.get("area") or "地区未知")[:100],
                "卖家昵称": str(card.get("seller") or "匿名卖家")[:100],
                "商品链接": link,
                "发布时间": str(card.get("publish_time") or "未知时间")[:100],
                "商品ID": item_id,
                "商品主图链接": image,
                "商品图片列表": [image] if image else [],
                "卖家信用等级": credit,
                "卖家信用来源": str(card.get("seller_credit_source") or "闲鱼搜索卡片信用徽标")[:80],
                "卖家注册时长": str(card.get("seller_registration") or "未知")[:80],
            }
        )
    return normalized


def _task_prompt(task: Task) -> str:
    if task.decision_mode != "ai":
        return ""
    base_path = Path(task.ai_prompt_base_file)
    criteria_path = Path(task.ai_prompt_criteria_file)
    if not base_path.is_file() or not criteria_path.is_file():
        raise ValueError("任务的 AI prompt 文件不存在")
    base = base_path.read_text(encoding="utf-8")
    criteria = criteria_path.read_text(encoding="utf-8")
    return base.replace("{{CRITERIA_SECTION}}", criteria)


async def ingest_search_cards(task: Task, cards: list[dict], *, run_id: str | None = None) -> dict:
    """Feed existing-Chrome search cards through the ordinary analysis and storage path."""
    from src.ai_handler import (
        download_all_images,
        get_ai_analysis,
        send_ntfy_notification,
    )
    from src.config import SKIP_AI_ANALYSIS
    from src.services.item_analysis_dispatcher import ItemAnalysisDispatcher, ItemAnalysisJob
    from src.services.price_history_service import (
        build_market_reference,
        load_price_snapshots,
        parse_price_value,
        record_market_snapshots,
    )
    from src.services.result_storage_service import (
        load_processed_link_keys,
        load_seller_credit_refresh_candidates,
        replace_seller_credit_analysis,
    )
    from src.utils import get_link_unique_key, save_to_jsonl

    prompt_text = _task_prompt(task)
    snapshot_time = datetime.now().isoformat()
    # Observe every search page before result deduplication: a known item may
    # have changed price even when it no longer needs an AI decision.
    record_market_snapshots(
        keyword=task.keyword,
        task_name=task.task_name,
        items=cards,
        run_id=run_id or uuid4().hex,
        snapshot_time=snapshot_time,
    )
    historical_snapshots = load_price_snapshots(task.keyword)
    processed_links = load_processed_link_keys(task.keyword)
    refresh_candidates = load_seller_credit_refresh_candidates(task.keyword) if task.decision_mode == "ai" else {}
    refreshing = {}
    selected = []
    for item in cards:
        key = get_link_unique_key(item["商品链接"])
        if key in processed_links:
            if key not in refresh_candidates or item.get("卖家信用等级", "未知") == "未知":
                continue
            refreshing[key] = refresh_candidates[key]
        price = parse_price_value(item["当前售价"])
        minimum = parse_price_value(task.min_price)
        maximum = parse_price_value(task.max_price)
        if price is None or (minimum is not None and price < minimum):
            continue
        if maximum is not None and price > maximum:
            continue
        selected.append(item)

    async def empty_seller_loader(_seller_id: str) -> dict:
        return {}

    async def save_or_refresh(record: dict, keyword: str) -> bool:
        previous = refreshing.get(get_link_unique_key(record["商品信息"]["商品链接"]))
        if previous:
            from src.ai_handler import validate_ai_response_format
            analysis = record.get("ai_analysis") or {}
            if analysis.get("error") or not validate_ai_response_format(analysis):
                return False
            return await replace_seller_credit_analysis(
                previous["id"], previous["raw_json"], record["卖家信息"], analysis
            )
        return await save_to_jsonl(record, keyword)

    async def notify_new_item(item: dict, reason: str):
        if get_link_unique_key(item["商品链接"]) not in refreshing:
            await send_ntfy_notification(item, reason)

    dispatcher = ItemAnalysisDispatcher(
        concurrency=2,
        skip_ai_analysis=SKIP_AI_ANALYSIS,
        seller_loader=empty_seller_loader,
        image_downloader=download_all_images,
        ai_analyzer=get_ai_analysis,
        notifier=notify_new_item,
        saver=save_or_refresh,
    )
    for item in selected:
        record = {
            "爬取时间": datetime.now().isoformat(),
            "搜索关键字": task.keyword,
            "任务名称": task.task_name,
            "商品信息": item,
            "卖家信息": {
                "卖家信用等级": item.get("卖家信用等级", "未知"),
                "卖家信用采集状态": "未采集" if item.get("卖家信用等级", "未知") == "未知" else "已采集",
                "卖家信用来源": item.get("卖家信用来源", "闲鱼搜索卡片信用徽标"),
                "卖家昵称": item.get("卖家昵称", "匿名卖家"),
                "卖家注册时长": item.get("卖家注册时长", "未知"),
            },
            "采集来源": "现有 Chrome 标签页",
        }
        previous = refreshing.get(get_link_unique_key(item["商品链接"]))
        if previous:
            original = json.loads(previous["raw_json"])
            record = {**original, "卖家信息": {**(original.get("卖家信息") or {}), **record["卖家信息"]}}
            record.pop("ai_analysis", None)
        price_reference = build_market_reference(
            keyword=task.keyword,
            item=record["商品信息"],
            current_market_items=cards,
            historical_snapshots=historical_snapshots,
        )
        record["价格参考"] = price_reference
        record["price_insight"] = price_reference.get("本商品价格位置", {})
        dispatcher.submit(
            ItemAnalysisJob(
                keyword=task.keyword,
                task_name=task.task_name,
                decision_mode=task.decision_mode,
                analyze_images=task.analyze_images,
                prompt_text=prompt_text,
                keyword_rules=tuple(task.keyword_rules),
                final_record=record,
                seller_id=None,
                zhima_credit_text=None,
                registration_duration_text="未知",
            )
        )
    await dispatcher.join()
    return {
        "received": len(cards),
        "selected": len(selected),
        "saved": dispatcher.completed_count,
    }
