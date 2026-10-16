import asyncio
from types import SimpleNamespace

import pytest

from src.services.chrome_browser_import import ingest_search_cards
from src.services.price_history_service import build_price_history_insights, load_price_snapshots


@pytest.fixture()
def chrome_market(tmp_path, monkeypatch):
    from src import ai_handler, config
    from src.services import chrome_browser_import

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_DATABASE_FILE", str(tmp_path / "test.sqlite3"))
    monkeypatch.setattr(config, "SKIP_AI_ANALYSIS", False)
    monkeypatch.setattr(chrome_browser_import, "_task_prompt", lambda _: "Analyze prices")
    analyzed = []

    async def analyze(record, _paths, _prompt):
        analyzed.append(record)
        return {"is_recommended": False, "reason": "test"}

    monkeypatch.setattr(ai_handler, "get_ai_analysis", analyze)
    task = SimpleNamespace(
        task_name="Watch", keyword="oppo watch x3", decision_mode="ai",
        analyze_images=False, keyword_rules=[], min_price=None, max_price=None,
    )

    def card(item_id, price):
        return {"商品ID": item_id, "商品标题": "OPPO Watch X3", "当前售价": f"¥{price}",
                "商品链接": f"https://www.goofish.com/item?id={item_id}"}

    return task, card, analyzed


def test_chrome_import_records_prices_and_supplies_ai_reference(chrome_market):
    task, card, analyzed = chrome_market
    result = asyncio.run(ingest_search_cards(task, [card("1", 1000), card("2", 2000)]))

    assert result["saved"] == 2
    insights = build_price_history_insights(task.keyword)
    assert insights["market_summary"]["sample_count"] == 2
    assert insights["market_summary"]["avg_price"] == 1500
    assert len(insights["daily_trend"]) == 1
    assert analyzed[0]["价格参考"]["当前搜索样本"]["avg_price"] == 1500
    assert analyzed[0]["price_insight"]["observation_count"] == 1


def test_chrome_reobserves_processed_items_without_reanalyzing(chrome_market):
    task, card, analyzed = chrome_market
    asyncio.run(ingest_search_cards(task, [card("1", 1000)]))
    result = asyncio.run(ingest_search_cards(task, [card("1", 900)]))

    assert result["selected"] == 0
    assert len(analyzed) == 1
    snapshots = load_price_snapshots(task.keyword)
    assert [s["price"] for s in snapshots] == [1000, 900]
    assert len({s["run_id"] for s in snapshots}) == 2


def test_chrome_pages_share_run_and_dedupe_repeated_cards(chrome_market):
    task, card, _ = chrome_market
    asyncio.run(ingest_search_cards(task, [card("1", 1000)], run_id="job-1"))
    asyncio.run(ingest_search_cards(task, [card("1", 1000), card("2", 2000)], run_id="job-1"))

    snapshots = load_price_snapshots(task.keyword)
    assert len(snapshots) == 2
    assert {s["run_id"] for s in snapshots} == {"job-1"}
    assert build_price_history_insights(task.keyword)["market_summary"]["sample_count"] == 2
