#!/usr/bin/env python3
"""llm_client 회귀 테스트. 네트워크 없이 가짜 응답 객체로 돈다.

여기서 잡으려는 것: Anthropic 과 OpenAI 의 응답 모양 차이를 흡수하는 부분.
특히 OpenAI 는 도구 인자가 JSON '문자열' 로 오고, 응답이 잘리면 그 문자열이
깨진 JSON 이 된다. 그때 예외로 죽으면 배치 전체가 날아간다.
"""
import json
import sys
from types import SimpleNamespace

import llm_client as lc

FAIL = []
def check(c, l):
    print(("  PASS  " if c else "  FAIL  ") + l)
    if not c:
        FAIL.append(l)


TOOL = {
    "name": "extract_tour_announcements",
    "description": "테스트용",
    "input_schema": {
        "type": "object",
        "properties": {"results": {"type": "array", "items": {"type": "object"}}},
        "required": ["results"],
    },
}
PAYLOAD = {"results": [{"tweet_id": "1", "is_relevant": True}]}


print("[프로바이더 판별]")
check(lc.resolve_provider("claude-sonnet-5") == lc.ANTHROPIC, "claude-* 는 Anthropic")
check(lc.resolve_provider("gpt-5.6-terra") == lc.OPENAI, "gpt-* 는 OpenAI")
check(lc.resolve_provider("GPT-5.6-Terra") == lc.OPENAI, "대소문자 무관")
try:
    lc.resolve_provider("llama-3")
    check(False, "모르는 모델은 예외")
except ValueError:
    check(True, "모르는 모델은 예외")


print("\n[도구 스키마 변환]")
t = lc.to_openai_tool(TOOL)
check(t["type"] == "function", "type=function")
check(t["function"]["name"] == TOOL["name"], "이름 보존")
check(t["function"]["parameters"] == TOOL["input_schema"], "input_schema -> parameters")
check("strict" not in t["function"],
      "strict 모드는 쓰지 않는다 (선택 필드를 required 로 만들면 결측을 지어낸다)")


class FakeAnthropic:
    def __init__(self, blocks, stop_reason=None):
        self._b, self._s = blocks, stop_reason
        self.messages = SimpleNamespace(create=self._create)
        self.kwargs = None
    def _create(self, **kw):
        self.kwargs = kw
        return SimpleNamespace(content=self._b, stop_reason=self._s)


class FakeOpenAI:
    def __init__(self, arguments, finish_reason="tool_calls", with_call=True):
        call = SimpleNamespace(function=SimpleNamespace(
            name=TOOL["name"], arguments=arguments))
        msg = SimpleNamespace(tool_calls=[call] if with_call else [])
        choice = SimpleNamespace(message=msg, finish_reason=finish_reason)
        self._r = SimpleNamespace(choices=[choice])
        self.kwargs = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
    def _create(self, **kw):
        self.kwargs = kw
        return self._r


print("\n[Anthropic 경로]")
blk = SimpleNamespace(type="tool_use", name=TOOL["name"], input=PAYLOAD)
c = FakeAnthropic([blk])
out, trunc = lc.call_tool(c, "claude-sonnet-5", "SYS", "USER", TOOL, 8000)
check(out == PAYLOAD, "tool_use.input 을 그대로 반환")
check(trunc is False, "stop_reason 없으면 잘림 아님")
check(c.kwargs["max_tokens"] == 8000, "max_tokens 파라미터 사용")
check(c.kwargs["system"] == "SYS", "system 은 별도 인자")
check(c.kwargs["tool_choice"] == {"type": "tool", "name": TOOL["name"]}, "도구 강제 호출")

_, trunc = lc.call_tool(FakeAnthropic([blk], "max_tokens"),
                        "claude-sonnet-5", "S", "U", TOOL, 10)
check(trunc is True, "stop_reason=max_tokens 를 잘림으로 인식")

out, _ = lc.call_tool(FakeAnthropic([SimpleNamespace(type="text", text="hi")]),
                      "claude-sonnet-5", "S", "U", TOOL, 10)
check(out is None, "tool_use 블록이 없으면 None")


print("\n[OpenAI 경로]")
c = FakeOpenAI(json.dumps(PAYLOAD))
out, trunc = lc.call_tool(c, "gpt-5.6-terra", "SYS", "USER", TOOL, 8000)
check(out == PAYLOAD, "arguments 문자열을 dict 로 파싱")
check(trunc is False, "finish_reason=tool_calls 는 잘림 아님")
check(c.kwargs["max_completion_tokens"] == 8000,
      "max_tokens 가 아니라 max_completion_tokens 를 쓴다")
check("max_tokens" not in c.kwargs, "max_tokens 는 보내지 않는다")
check(c.kwargs["messages"][0] == {"role": "system", "content": "SYS"},
      "system 을 messages 맨 앞에 넣는다 (프롬프트 캐싱은 공통 접두사 기준)")
check(c.kwargs["messages"][1]["content"] == "USER", "user 는 그 다음")
check(c.kwargs["tool_choice"]["function"]["name"] == TOOL["name"], "도구 강제 호출")

_, trunc = lc.call_tool(FakeOpenAI(json.dumps(PAYLOAD), "length"),
                        "gpt-5.6-terra", "S", "U", TOOL, 10)
check(trunc is True, "finish_reason=length 를 잘림으로 인식")

# 핵심 케이스: 응답이 잘려 JSON 이 깨진 경우
broken = '{"results": [{"tweet_id": "1", "is_rele'
out, trunc = lc.call_tool(FakeOpenAI(broken, "length"), "gpt-5.6-terra", "S", "U", TOOL, 10)
check(isinstance(out, dict), "깨진 JSON 에도 예외를 던지지 않는다")
check(out.get("results") == broken,
      "원본 문자열을 첫 프로퍼티에 담아 호출부의 부분 복구에 맡긴다")
check(trunc is True, "잘림 표시는 유지")

out, _ = lc.call_tool(FakeOpenAI("", with_call=False), "gpt-5.6-terra", "S", "U", TOOL, 10)
check(out is None, "도구 호출이 없으면 None")


print("\n[깨진 문자열이 실제로 복구되는가]")
# llm_client 가 넘긴 모양이 curate_tour._coerce_results 로 실제 복구되는지 확인한다.
# 이 연결이 끊기면 OpenAI 로 바꾼 순간 잘린 배치가 통째로 유실된다.
import curate_tour as ct
partial = ('[{"tweet_id": "1", "is_relevant": true}, '
           '{"tweet_id": "2", "is_relevant": false}, {"tweet_id": "3", "is_rel')
rec = ct._coerce_results(partial, "test")
check(rec is not None and len(rec) == 2,
      f"잘린 배열에서 온전한 항목만 건져낸다 (복구 {len(rec) if rec else 0}건)")


print("\n[오류 분류]")
def exc(code):
    e = RuntimeError("boom")
    e.status_code = code
    return e
check(lc.is_permanent(exc(400)) is True, "400 은 재시도 무의미")
check(lc.is_permanent(exc(401)) is True, "401 은 재시도 무의미")
check(lc.is_permanent(exc(429)) is False, "429 는 재시도 대상")
check(lc.is_permanent(exc(408)) is False, "408 은 재시도 대상")
check(lc.is_permanent(exc(500)) is False, "5xx 는 재시도 대상")
check(lc.is_permanent(RuntimeError("네트워크")) is False, "상태 코드 없으면 재시도")
check("400" in lc.err_detail(exc(400)), "err_detail 에 상태 코드가 들어간다")

print()
if FAIL:
    print("실패:", FAIL)
    sys.exit(1)
print("전체 통과")
