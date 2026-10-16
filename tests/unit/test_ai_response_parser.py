import pytest
import json
from types import SimpleNamespace

from src.services.ai_response_parser import (
    extract_ai_response_content,
    parse_ai_response_json,
    EmptyAIResponseError,
)


def test_parse_ai_response_json_uses_first_object_when_multiple_json_objects_are_concatenated():
    content = """```json
{"is_recommended": true, "reason": "first"}
{"is_recommended": false, "reason": "second"}
```"""

    result = parse_ai_response_json(content)

    assert result == {"is_recommended": True, "reason": "first"}


def test_parse_ai_response_json_extracts_json_from_wrapped_text():
    content = """分析结果如下：

```json
{"is_recommended": true, "reason": "wrapped"}
```

请按第一份结果处理。"""

    result = parse_ai_response_json(content)

    assert result == {"is_recommended": True, "reason": "wrapped"}


def test_parse_ai_response_json_raises_when_no_json_exists():
    with pytest.raises(ValueError):
        parse_ai_response_json("没有任何 JSON 内容")


def test_extract_ai_response_content_with_none_content_but_valid_reasoning_content():
    """当 content 为 None 但 reasoning_content 有值时，应该成功提取 reasoning_content 的内容"""
    # 创建 mock 对象模拟 OpenAI 风格的响应
    message = type('Message', (), {
        'content': None,
        'reasoning_content': '这是推理内容'
    })()
    choice = type('Choice', (), {'message': message})()
    response = type('Response', (), {'choices': [choice]})()

    result = extract_ai_response_content(response)

    assert result == '这是推理内容'


def test_extract_ai_response_content_raises_when_content_and_reasoning_content_are_empty():
    """当 content 和 reasoning_content 都为空时，应该抛出 EmptyAIResponseError"""
    # 创建 mock 对象
    message = type('Message', (), {
        'content': None,
        'reasoning_content': None
    })()
    choice = type('Choice', (), {'message': message})()
    response = type('Response', (), {'choices': [choice]})()

    with pytest.raises(EmptyAIResponseError):
        extract_ai_response_content(response)


def test_truncated_response_is_rejected_before_reading_reasoning_json():
    response = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="length",
        message=SimpleNamespace(content=None, reasoning_content='{"is_recommended":true}'),
    )])
    with pytest.raises(ValueError, match="截断"):
        extract_ai_response_content(response)


def test_incomplete_responses_api_output_is_rejected():
    response = SimpleNamespace(
        status="incomplete", incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        output_text='{"is_recommended":true}',
    )
    with pytest.raises(ValueError, match="截断"):
        extract_ai_response_content(response)


def test_analysis_can_require_final_content_without_reasoning_fallback():
    response = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=None, reasoning_content='{"is_recommended":true}'),
    )])
    with pytest.raises(EmptyAIResponseError):
        extract_ai_response_content(response, allow_reasoning_fallback=False)


def test_truncated_outer_json_is_not_replaced_by_a_nested_object():
    content = '{"prompt_version":"v1","criteria_analysis":{"seller_type":{"status":"PASS"}}'
    with pytest.raises(json.JSONDecodeError):
        parse_ai_response_json(content)
