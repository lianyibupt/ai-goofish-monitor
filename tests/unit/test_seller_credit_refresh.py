import asyncio
import json


def test_refresh_credit_updates_analysis_without_duplicating_or_unhiding(tmp_path, monkeypatch):
    from src.infrastructure.persistence.sqlite_connection import sqlite_connection
    from src.services import result_storage_service as storage
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('APP_DATABASE_FILE', str(tmp_path/'test.sqlite3'))
    record = {'任务名称':'test','搜索关键字':'test','爬取时间':'2026-10-15',
        '商品信息':{'商品ID':'123','商品链接':'https://www.goofish.com/item?id=123','商品标题':'OPPO','当前售价':'¥100'},
        '卖家信息':{'卖家芝麻信用':None},'ai_analysis':{'is_recommended':False,'reason':'卖家信用未知',
            'criteria_analysis':{'seller_credit':{'status':'FAIL'}}}}
    asyncio.run(storage.save_result_record(record,'test'))
    with sqlite_connection() as conn:
        conn.execute("UPDATE result_items SET status='hidden'");conn.commit()
    candidates = storage.load_seller_credit_refresh_candidates('test')
    old = candidates['https://www.goofish.com/item?id=123']
    analysis = {'prompt_version':'v1','is_recommended':False,'reason':'其他要求待核实','risk_tags':[],
        'criteria_analysis':{'seller_credit':{'status':'PASS'},'seller_type':{'status':'NEEDS_MANUAL_CHECK'}}}
    seller = {'卖家信用等级':'卖家信用优秀','卖家信用来源':'闲鱼搜索卡片信用徽标'}
    assert asyncio.run(storage.replace_seller_credit_analysis(old['id'],old['raw_json'],seller,analysis))
    with sqlite_connection() as conn:
        rows = conn.execute('SELECT * FROM result_items').fetchall()
    assert len(rows) == 1
    updated = json.loads(rows[0]['raw_json'])
    assert rows[0]['status'] == 'hidden'
    assert updated['商品信息'] == record['商品信息']
    assert updated['爬取时间'] == record['爬取时间']
    assert updated['卖家信息']['卖家信用等级'] == '卖家信用优秀'
    assert updated['ai_analysis']['criteria_analysis']['seller_credit']['status'] == 'PASS'
    assert not asyncio.run(storage.replace_seller_credit_analysis(old['id'],old['raw_json'],seller,analysis))
    assert storage.load_seller_credit_refresh_candidates('test') == {}


def test_chrome_reimport_reanalyzes_missing_credit_once_without_notification(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src import ai_handler, config
    from src.services import chrome_browser_import as bridge, result_storage_service as storage
    from src.infrastructure.persistence.sqlite_connection import sqlite_connection
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('APP_DATABASE_FILE',str(tmp_path/'import.sqlite3'))
    monkeypatch.setattr(config,'SKIP_AI_ANALYSIS',False)
    monkeypatch.setattr(bridge,'_task_prompt',lambda _: 'JSON')
    task=SimpleNamespace(task_name='test',keyword='test',decision_mode='ai',analyze_images=False,
        keyword_rules=[],min_price=None,max_price=None)
    record={'任务名称':'test','搜索关键字':'test','爬取时间':'old-time','商品信息':{'商品ID':'123',
        '商品链接':'https://www.goofish.com/item?id=123','商品标题':'old title','当前售价':'¥100'},
        '卖家信息':{},'ai_analysis':{'is_recommended':False,'criteria_analysis':{'seller_credit':{'status':'FAIL'}}}}
    asyncio.run(storage.save_result_record(record,'test'))
    calls=[]
    async def ai(record,paths,prompt):
        calls.append(record)
        assert record['卖家信息']['卖家信用等级']=='卖家信用优秀'
        assert record['商品信息']['商品标题']=='old title'
        return {'prompt_version':'v1','is_recommended':True,'reason':'通过','risk_tags':[],
            'criteria_analysis':{'seller_type':{'status':'PASS'},'seller_credit':{'status':'PASS'}}}
    async def notify(*args):
        raise AssertionError('补跑历史记录不应发送通知')
    monkeypatch.setattr(ai_handler,'get_ai_analysis',ai)
    monkeypatch.setattr(ai_handler,'send_ntfy_notification',notify)
    cards=bridge.normalize_search_cards(page_url='https://www.goofish.com/search?q=test',expected_keyword='test',
        cards=[{'url':'https://www.goofish.com/item?id=123','title':'new title','price':'¥100','seller_credit':'卖家信用优秀'}])
    first=asyncio.run(bridge.ingest_search_cards(task,cards))
    second=asyncio.run(bridge.ingest_search_cards(task,cards))
    assert first['saved']==1 and second['selected']==0 and len(calls)==1
    with sqlite_connection() as conn:
        rows=conn.execute('SELECT raw_json FROM result_items').fetchall()
    assert len(rows)==1
    assert json.loads(rows[0]['raw_json'])['ai_analysis']['is_recommended'] is True
