import asyncio
import json


def test_retry_updates_only_failed_analysis_and_preserves_visibility(tmp_path, monkeypatch):
    from src.infrastructure.persistence.sqlite_connection import sqlite_connection
    from src.services import result_storage_service as storage

    monkeypatch.setenv("APP_DATABASE_FILE", str(tmp_path / "repair.sqlite3"))
    monkeypatch.chdir(tmp_path)
    record = {
        "爬取时间": "2026-10-15T22:00:00", "任务名称": "test", "搜索关键字": "test",
        "商品信息": {"商品ID": "123", "商品标题": "test", "商品链接": "https://www.goofish.com/item?id=123", "当前售价": "¥100"},
        "ai_analysis": {"analysis_source": "ai", "is_recommended": False, "error": "output truncated"},
    }
    asyncio.run(storage.save_result_record(record, "test"))
    with sqlite_connection() as conn:
        row = conn.execute("SELECT id,raw_json FROM result_items").fetchone()
        conn.execute("UPDATE result_items SET status='hidden'")
        conn.commit()
    analysis = {"prompt_version": "v1", "is_recommended": False, "reason": "evidence missing", "risk_tags": [], "criteria_analysis": {"seller_type": {"status": "UNKNOWN"}}}
    assert asyncio.run(storage.replace_failed_ai_analysis(row["id"], row["raw_json"], analysis))
    with sqlite_connection() as conn:
        updated = conn.execute("SELECT raw_json,status,analysis_source FROM result_items").fetchone()
    data = json.loads(updated["raw_json"])
    assert data["商品信息"] == record["商品信息"]
    assert data["爬取时间"] == record["爬取时间"]
    assert "error" not in data["ai_analysis"]
    assert updated["status"] == "hidden"
    assert updated["analysis_source"] == "ai"
    # A concurrent update, or an already repaired result, must not be overwritten.
    assert not asyncio.run(storage.replace_failed_ai_analysis(row["id"], row["raw_json"], analysis))
    assert not asyncio.run(storage.replace_failed_ai_analysis(row["id"], updated["raw_json"], analysis))
