"""Read-only tools for the CURRENT backend-provided snapshot. No balance mutation."""
from app.modules.agent.tools import Tool, ToolContext, ToolRegistry, InvalidToolArguments

EMPTY_PARAMETERS = {"type": "object", "properties": {}, "additionalProperties": False}

def no_arguments(arguments: dict) -> dict:
    if arguments:
        raise InvalidToolArguments("This tool has no model-supplied arguments")
    return {}

async def get_blocks(arguments: dict, context: ToolContext) -> dict:
    blocks = context.snapshot.get("blocks")
    return {"available": blocks is not None, "source": "current_request_snapshot", "blocks": blocks}

async def get_waiting_funds(arguments: dict, context: ToolContext) -> dict:
    funds = context.snapshot.get("waitingFunds")
    return {"available": funds is not None, "source": "current_request_snapshot", "waitingFunds": funds}

def build_fintris_chat_tools() -> ToolRegistry:
    return ToolRegistry([
        Tool("get_blocks", "백엔드가 이번 요청에 전달한 사용자의 블록 목록·잔액을 조회한다. 실시간 API 재조회가 아니며 available=false는 정보 미제공이다.", EMPTY_PARAMETERS, no_arguments, get_blocks),
        Tool("get_waiting_funds", "백엔드가 이번 요청에 전달한 미배분 자금을 조회한다. 실제 자금을 이동하거나 분배하지 않는다.", EMPTY_PARAMETERS, no_arguments, get_waiting_funds),
    ])
