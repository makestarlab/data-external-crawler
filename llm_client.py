#!/usr/bin/env python3
"""추출용 LLM 호출을 프로바이더 무관하게 감싼다.

왜 있나
-------
2026-09-03, 회사 방침으로 추출 모델을 Claude 에서 OpenAI GPT-5.6 Terra 로 옮기게 됐다.
curate_events / curate_tour / evaluate_curation 세 곳이 각자 anthropic SDK 를 직접
불러 쓰고 있어서, 그대로 두면 같은 수정을 세 번 하고 앞으로도 세 번씩 해야 한다.

호출 규약을 여기 한 곳에 모으고, 모델 이름만 보고 프로바이더를 고르게 했다.
  claude-*                  -> Anthropic
  gpt-*, o1/o3/o4-*         -> OpenAI
CURATION_MODEL / TOUR_CURATION_MODEL 값만 바꾸면 코드 수정 없이 왔다갔다 할 수 있다.
계약 문제로 옮기는 것이라 언제든 되돌릴 여지를 남겨야 한다.

두 API 의 실질적인 차이 (여기서 흡수한다)
------------------------------------------
0. 엔드포인트
     Anthropic /v1/messages
     OpenAI    /v1/responses  <- Chat Completions 가 아니다

   [2026-09-03] 처음엔 chat.completions 로 짰다가 첫 eval 에서 60건 전부 HTTP 400 이
   났다. 서버 메시지가 정확했다:
     "Function tools with reasoning_effort are not supported for gpt-5.6-terra
      in /v1/chat/completions. To use function tools, use /v1/responses or set
      reasoning_effort to 'none'."
   GPT-5.6 계열은 추론 모델이라 도구 호출을 Responses API 에서 해야 한다.
   reasoning_effort 를 'none' 으로 꺼서 Chat Completions 를 쓰는 길도 있지만,
   우리가 시키는 건 "이게 투어 공지인가" 라는 판정이라 추론을 끄면 그만큼 손해다.

1. 도구 스키마 모양
     Anthropic {name, description, input_schema}
     OpenAI    {type:'function', name, description, parameters}
   Responses API 는 Chat Completions 와 달리 function 키로 한 번 더 감싸지 않는다.
2. 강제 호출
     Anthropic tool_choice={'type':'tool', 'name': ...}
     OpenAI    tool_choice={'type':'function', 'name': ...}
3. 프롬프트를 넣는 자리
     Anthropic system= / messages=
     OpenAI    instructions= / input=
4. 결과를 꺼내는 위치
     Anthropic resp.content[i].input  -> 이미 dict
     OpenAI    resp.output[i] 중 type=='function_call' 인 항목의 .arguments -> JSON '문자열'
   OpenAI 쪽은 파싱이 한 번 더 필요하고, 응답이 잘리면 그 문자열이 깨진 JSON 으로 온다.
   이때 예외로 죽이지 않고 문자열 그대로 얹어서 돌려준다. 호출부의 _coerce_results 가
   잘린 문자열에서 온전한 항목만 건져내는 로직을 이미 갖고 있다 (2026-08-13, 08-24).
5. 잘림 신호
     Anthropic stop_reason == 'max_tokens'
     OpenAI    status == 'incomplete'
6. 출력 상한 파라미터 이름
     Anthropic max_tokens
     OpenAI    max_output_tokens
   추론 모델은 추론 토큰도 이 상한을 먹는다. 같은 값이면 실제로 쓸 수 있는 출력이
   줄어들 수 있어서, 잘림 로그가 늘면 상한을 올려야 한다.
7. 프롬프트 캐싱
     Anthropic 는 cache_control 로 명시해야 하고, OpenAI 는 1024 토큰 이상 공통 접두사를
     자동으로 캐싱한다. 우리 system 프롬프트는 호출마다 동일하므로 OpenAI 에서는
     따로 할 게 없다. 대신 system 을 messages 맨 앞에 두는 순서를 깨면 안 된다.

Structured Outputs 의 strict 모드는 쓰지 않는다. strict 는 모든 필드를 required 로
요구하는데, 우리 스키마는 venue_name·city 처럼 "모르면 비워두는" 필드가 핵심이다.
억지로 required 로 만들면 모델이 빈 문자열을 지어내게 되고, 그건 결측보다 나쁘다.
"""
import json
import logging
import os

log = logging.getLogger(__name__)

ANTHROPIC = "anthropic"
OPENAI = "openai"


def resolve_provider(model):
    """모델 이름만 보고 프로바이더를 정한다."""
    m = (model or "").lower()
    if m.startswith("claude"):
        return ANTHROPIC
    if m.startswith(("gpt-", "gpt", "o1", "o3", "o4", "chatgpt")):
        return OPENAI
    raise ValueError(
        f"모델 '{model}' 의 프로바이더를 알 수 없다. "
        "claude-* 또는 gpt-* 형태여야 한다.")


def to_openai_tool(tool_schema):
    """Anthropic 모양 도구 스키마를 OpenAI Responses API 모양으로 바꾼다.

    Chat Completions 는 {'type':'function','function':{...}} 처럼 한 번 더 감싸지만
    Responses 는 평평하다. 감싼 채로 보내면 name 을 못 찾는다.
    """
    return {
        "type": "function",
        "name": tool_schema["name"],
        "description": tool_schema.get("description", ""),
        "parameters": tool_schema["input_schema"],
    }


def get_client(model):
    provider = resolve_provider(model)
    if provider == ANTHROPIC:
        import anthropic
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY 가 없다")
        return anthropic.Anthropic(api_key=key)
    import openai
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY 가 없다")
    # base_url 을 열어두는 이유: 사내 게이트웨이나 Azure 경유로 바뀔 수 있다.
    base_url = os.environ.get("OPENAI_BASE_URL") or None
    return openai.OpenAI(api_key=key, base_url=base_url)


def call_tool(client, model, system, user, tool_schema, max_tokens):
    """도구 호출을 강제해서 한 번 부른다.

    반환: (payload, truncated)
      payload   도구 인자. 보통 dict. OpenAI 응답이 잘려 JSON 파싱이 안 되면
                {'<첫 프로퍼티명>': '<원본 문자열>'} 형태로 돌려준다 - 호출부의
                복구 로직이 문자열에서 건져낼 수 있게 하려는 것이다.
                도구를 아예 안 부른 경우 None.
      truncated 출력 상한에 걸려 잘렸으면 True.
    예외는 그대로 위로 올린다. 재시도 정책은 호출부가 각자 갖고 있다.
    """
    if resolve_provider(model) == ANTHROPIC:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": tool_schema["name"]},
            messages=[{"role": "user", "content": user}],
        )
        truncated = getattr(resp, "stop_reason", None) == "max_tokens"
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                return block.input, truncated
        return None, truncated

    kwargs = dict(
        model=model,
        max_output_tokens=max_tokens,
        instructions=system,
        input=user,
        tools=[to_openai_tool(tool_schema)],
        tool_choice={"type": "function", "name": tool_schema["name"]},
    )
    # 추론 강도는 기본값에 맡긴다. 판정 품질이 걸린 부분이라 임의로 낮추지 않는다.
    # 비용이나 지연이 문제가 되면 OPENAI_REASONING_EFFORT 로 조절한다.
    effort = os.environ.get("OPENAI_REASONING_EFFORT")
    if effort:
        kwargs["reasoning"] = {"effort": effort}

    resp = client.responses.create(**kwargs)

    truncated = getattr(resp, "status", None) == "incomplete"
    if truncated:
        detail = getattr(resp, "incomplete_details", None)
        log.warning("OpenAI 응답이 incomplete 로 끝났다: %s", detail)

    raw = None
    for item in (getattr(resp, "output", None) or []):
        if getattr(item, "type", None) == "function_call":
            raw = getattr(item, "arguments", None)
            break
    if raw is None:
        return None, truncated
    try:
        return json.loads(raw), truncated
    except (json.JSONDecodeError, TypeError):
        # 잘린 JSON. 여기서 죽이면 배치 전체가 날아간다. 원본 문자열을 스키마의
        # 첫 프로퍼티에 얹어 돌려주면 호출부가 부분 복구를 시도할 수 있다.
        props = tool_schema.get("input_schema", {}).get("properties", {})
        key = next(iter(props), "results")
        log.warning("OpenAI 도구 인자 JSON 파싱 실패 (길이 %d). "
                    "원본 문자열을 '%s' 에 담아 부분 복구를 맡긴다.",
                    len(raw or ""), key)
        return {key: raw}, truncated


def status_code(exc):
    """프로바이더가 달라도 HTTP 상태 코드를 같은 방법으로 꺼낸다."""
    for attr in ("status_code", "http_status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else None


def is_permanent(exc):
    """재시도해도 같은 답이 오는 오류인가.

    400 계열은 요청 자체가 잘못된 것이라 백오프가 의미 없다. 단 408(timeout)과
    429(rate limit)는 예외 - 이건 기다리면 풀린다.
    """
    code = status_code(exc)
    if code is None:
        return False
    return 400 <= code < 500 and code not in (408, 409, 429)


def err_detail(exc, limit=300):
    code = status_code(exc)
    body = getattr(exc, "message", None) or str(exc)
    head = f"[{type(exc).__name__}"
    if code is not None:
        head += f" {code}"
    return f"{head}] {str(body)[:limit]}"
