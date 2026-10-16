import asyncio

from src.infrastructure.persistence.sqlite_bootstrap import bootstrap_sqlite_storage
from src.infrastructure.persistence.sqlite_connection import sqlite_connection
from src.services.price_history_service import load_price_snapshots, record_market_snapshots
from src.services.result_storage_service import save_result_record


def test_bootstrap_recovers_missing_history_once_with_original_dates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_DATABASE_FILE", str(tmp_path / "test.sqlite3"))
    bootstrap_sqlite_storage()
    for item_id, time, price in [("1", "2026-10-15T12:00:00", "¥1000"),
                                 ("2", "2026-10-16T12:00:00", "¥1500"),
                                 ("3", "invalid-time", "¥2000"),
                                 ("4", "2026-10-16T12:00:00", "暂无")]:
        asyncio.run(save_result_record({
            "爬取时间": time, "任务名称": "Watch", "搜索关键字": "oppo watch x3",
            "商品信息": {"商品ID": item_id, "商品标题": "Watch", "当前售价": price,
                         "商品链接": f"https://www.goofish.com/item?id={item_id}"},
        }, "oppo watch x3"))

    # Model an existing installation upgrading to the recovery migration.
    with sqlite_connection() as conn:
        conn.execute("DELETE FROM app_metadata WHERE key = 'migration:result_price_snapshots'")
        conn.commit()
    bootstrap_sqlite_storage()
    bootstrap_sqlite_storage()
    snapshots = load_price_snapshots("oppo watch x3")
    assert [(s["snapshot_time"], s["price"]) for s in snapshots] == [
        ("2026-10-15T12:00:00", 1000), ("2026-10-16T12:00:00", 1500)]
    assert all(s["run_id"].startswith("recovered:") for s in snapshots)


def test_recovery_preserves_existing_market_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_DATABASE_FILE", str(tmp_path / "test.sqlite3"))
    item = {"商品ID": "1", "当前售价": "¥800", "商品链接": "https://www.goofish.com/item?id=1"}
    record_market_snapshots(keyword="watch", task_name="Watch", items=[item],
                            run_id="real-run", snapshot_time="2026-10-15T12:00:00")
    asyncio.run(save_result_record({"爬取时间": "2026-10-16T12:00:00", "商品信息": item}, "watch"))
    with sqlite_connection() as conn:
        conn.execute("DELETE FROM app_metadata WHERE key = 'migration:result_price_snapshots'")
        conn.commit()
    bootstrap_sqlite_storage()
    assert [s["run_id"] for s in load_price_snapshots("watch")] == ["real-run"]
