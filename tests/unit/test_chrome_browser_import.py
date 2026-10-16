import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_normalize_existing_chrome_search_cards_accepts_matching_search():
    from src.services.chrome_browser_import import normalize_search_cards

    cards = normalize_search_cards(
        page_url="https://www.goofish.com/search?q=iPad%20air",
        expected_keyword="iPad air",
        cards=[
            {
                "url": "https://www.goofish.com/item?id=12345&categoryId=1",
                "title": "iPad Air 5 64G",
                "price": "¥ 1200",
                "area": "北京",
                "image_url": "https://img.alicdn.com/item.webp",
            }
        ],
    )

    assert cards[0]["商品ID"] == "12345"
    assert cards[0]["商品链接"] == "https://www.goofish.com/item?id=12345&categoryId=1"
    assert cards[0]["商品标题"] == "iPad Air 5 64G"
    assert cards[0]["当前售价"] == "¥ 1200"


@pytest.mark.parametrize(
    "page_url, item_url",
    [
        ("https://evil.example/search?q=iPad%20air", "https://www.goofish.com/item?id=12345"),
        ("https://www.goofish.com/search?q=other", "https://www.goofish.com/item?id=12345"),
        ("https://www.goofish.com/search?q=iPad%20air", "https://evil.example/item?id=12345"),
    ],
)
def test_normalize_existing_chrome_search_cards_rejects_mismatched_source(page_url, item_url):
    from src.services.chrome_browser_import import normalize_search_cards

    with pytest.raises(ValueError):
        normalize_search_cards(
            page_url=page_url,
            expected_keyword="iPad air",
            cards=[{"url": item_url, "title": "iPad", "price": "¥ 1"}],
        )


def test_bridge_token_is_stable_and_private(tmp_path):
    from src.services.chrome_browser_import import ensure_bridge_config, valid_bridge_token

    path = tmp_path / "bridge-config.json"
    first = ensure_bridge_config(path)
    second = ensure_bridge_config(path)

    assert first["token"] == second["token"]
    assert valid_bridge_token(first["token"], path)
    assert not valid_bridge_token("invalid", path)
    assert path.stat().st_mode & 0o077 == 0


def test_bridge_endpoint_requires_pairing_and_matching_search(monkeypatch):
    from types import SimpleNamespace
    from src.api.routes import chrome_bridge
    from src.api.dependencies import get_process_service
    from src.services.process_service import ProcessService

    app = FastAPI()
    app.dependency_overrides[get_process_service] = ProcessService
    app.include_router(chrome_bridge.router)
    monkeypatch.setattr(chrome_bridge, "valid_bridge_token", lambda token: token == "valid")
    monkeypatch.setattr(
        chrome_bridge,
        "find_task_by_name_sync",
        lambda name: SimpleNamespace(task_name=name, enabled=True, keyword="iPad air"),
    )

    async def fake_ingest(task, cards):
        assert task.task_name == "iPad air"
        assert cards[0]["商品ID"] == "12345"
        return {"received": 1, "selected": 1, "saved": 1}

    monkeypatch.setattr(chrome_bridge, "ingest_search_cards", fake_ingest)
    client = TestClient(app)
    payload = {
        "task_name": "iPad air",
        "page_url": "https://www.goofish.com/search?q=iPad%20air",
        "cards": [
            {
                "url": "https://www.goofish.com/item?id=12345",
                "title": "iPad",
                "price": "¥ 1200",
            }
        ],
    }

    assert client.post("/api/chrome-bridge/import", json=payload).status_code == 403
    headers = {"X-Chrome-Bridge-Token": "valid"}
    response = client.post("/api/chrome-bridge/import", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["saved"] == 1
    payload["page_url"] = "https://www.goofish.com/search?q=other"
    assert client.post("/api/chrome-bridge/import", json=payload, headers=headers).status_code == 400


def test_bridge_queue_endpoints_claim_once_and_complete(monkeypatch):
    from types import SimpleNamespace
    from src.api.dependencies import get_process_service
    from src.api.routes import chrome_bridge
    from src.services.process_service import ProcessService

    service = ProcessService()
    monkeypatch.setattr(service, "_write_chrome_log", lambda *_args: None)
    job = service.chrome_queue.enqueue(
        SimpleNamespace(
            id=4, task_name="iPad air", keyword="iPad air", max_pages=2,
            personal_only=True, free_shipping=True, min_price="500", max_price="2000",
        )
    )
    monkeypatch.setattr(chrome_bridge, "valid_bridge_token", lambda token: token == "valid")
    app = FastAPI()
    app.dependency_overrides[get_process_service] = lambda: service
    app.include_router(chrome_bridge.router)
    client = TestClient(app)
    headers = {"X-Chrome-Bridge-Token": "valid"}

    assert client.get("/api/chrome-bridge/next").status_code == 403
    claimed = client.get("/api/chrome-bridge/next", headers=headers).json()["job"]
    assert claimed["job_id"] == job.id
    assert client.get("/api/chrome-bridge/next", headers=headers).json()["job"] is None
    completed = client.post(
        "/api/chrome-bridge/complete", json={"job_id": job.id}, headers=headers
    )
    assert completed.status_code == 200
    assert service.chrome_queue.get(job.id) is None


def test_scheduled_import_acknowledges_before_slow_analysis(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from src.api.routes import chrome_bridge
    from src.services.process_service import ProcessService

    async def scenario():
        service = ProcessService()
        monkeypatch.setattr(service, "_write_chrome_log", lambda *_args: None)
        task = SimpleNamespace(
            id=4, task_name="iPad air", enabled=True, keyword="iPad air", max_pages=1,
            personal_only=True, free_shipping=True, min_price="500", max_price="2000",
        )
        job = service.chrome_queue.enqueue(task)
        service.claim_chrome_job()
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_ingest(_task, cards, *, run_id):
            assert run_id == job.id
            entered.set()
            await release.wait()
            return {"received": len(cards), "selected": 1, "saved": 1}

        monkeypatch.setattr(chrome_bridge, "valid_bridge_token", lambda _: True)
        monkeypatch.setattr(chrome_bridge, "find_task_by_name_sync", lambda _: task)
        monkeypatch.setattr(chrome_bridge, "ingest_search_cards", slow_ingest)
        response = await chrome_bridge.import_search(
            chrome_bridge.SearchImport(
                task_name=task.task_name, job_id=job.id,
                page_url="https://www.goofish.com/search?q=iPad%20air",
                cards=[{"url": "https://www.goofish.com/item?id=12345", "title": "iPad", "price": "¥1200"}],
            ),
            x_chrome_bridge_token="valid", process_service=service,
        )
        assert response.status_code == 202
        await entered.wait()
        assert service.is_running(4)
        release.set()
        await service.finish_chrome_job(job.id)
        await service.chrome_finalizers[job.id]
        assert not service.is_running(4)

    asyncio.run(scenario())


def test_normalize_search_cards_keeps_credit_badge_separate_from_zhima():
    from src.services.chrome_browser_import import normalize_search_cards
    card = {"url": "https://www.goofish.com/item?id=123", "title": "OPPO", "price": "¥100", "seller_credit": "卖家信用优秀"}
    result = normalize_search_cards(page_url="https://www.goofish.com/search?q=OPPO", expected_keyword="OPPO", cards=[card])[0]
    assert result["卖家信用等级"] == "卖家信用优秀"
    card["seller_credit"] = "百分百好评"
    result = normalize_search_cards(page_url="https://www.goofish.com/search?q=OPPO", expected_keyword="OPPO", cards=[card])[0]
    assert result["卖家信用等级"] == "未知"
