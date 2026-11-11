"""Fintris snapshot tools and a proposal builder. No HTTP/DB/balance mutation."""
from app.modules.agent.tools import (
    InvalidToolArguments, Tool, ToolContext, ToolRegistry, ToolRejected,
)
from app.schemas.coach_chat import TransferProposal

TRANSFER_TOOL_NAME = "propose_block_transfer"
EMPTY_PARAMETERS = {"type": "object", "properties": {}, "additionalProperties": False}
TRANSFER_PARAMETERS = {
    "type": "object",
    "properties": {
        "from_block_id": {"type": "integer", "minimum": 1, "description": "출발 블록 ID"},
        "to_block_id": {"type": "integer", "minimum": 1, "description": "도착 블록 ID"},
        "amount": {"type": "integer", "minimum": 1, "description": "이동할 원화 금액(정수)"},
    },
    "required": ["from_block_id", "to_block_id", "amount"],
    "additionalProperties": False,
}


def no_arguments(arguments: dict) -> dict:
    if arguments:
        raise InvalidToolArguments("This tool has no model-supplied arguments")
    return {}


def transfer_arguments(arguments: dict) -> dict:
    if set(arguments) != {"from_block_id", "to_block_id", "amount"}:
        raise InvalidToolArguments("Only the two block IDs and amount are allowed")
    # Strict integers exclude True, floats, strings, zero and negative amounts.
    return TransferProposal.model_validate(arguments).model_dump()


async def get_blocks(arguments: dict, context: ToolContext) -> dict:
    blocks = context.snapshot.get("blocks")
    return {"available": blocks is not None, "source": "current_request_snapshot", "blocks": blocks}


async def get_waiting_funds(arguments: dict, context: ToolContext) -> dict:
    funds = context.snapshot.get("waitingFunds")
    return {"available": funds is not None, "source": "current_request_snapshot", "waitingFunds": funds}


async def propose_block_transfer(arguments: dict, context: ToolContext) -> dict:
    proposal = TransferProposal.model_validate(arguments)
    blocks = context.snapshot.get("blocks")
    if blocks is None:
        raise ToolRejected("BLOCKS_NOT_PROVIDED")
    source = [b for b in blocks if b.get("blockId") == proposal.from_block_id]
    target = [b for b in blocks if b.get("blockId") == proposal.to_block_id]
    if not source or not target:
        raise ToolRejected("BLOCK_NOT_FOUND")
    if len(source) != 1 or len(target) != 1:
        raise ToolRejected("AMBIGUOUS_BLOCK_ID")
    # Deliberately do not check mutable backend rules or claim approval here.
    # Spring must check ownership, latest balance/status and approval itself.
    return {
        "status": "PROPOSAL_PREPARED",
        "requiresUserConfirmation": True,
        "transfer": proposal.model_dump(by_alias=True),
        "fromBlockName": source[0]["name"],
        "toBlockName": target[0]["name"],
    }


def build_fintris_chat_tools() -> ToolRegistry:
    return ToolRegistry([
        Tool(
            "get_blocks",
            "백엔드가 이번 요청에 전달한 블록 목록·잔액을 조회한다. 실시간 재조회가 아니며 available=false는 정보 미제공이다.",
            EMPTY_PARAMETERS, no_arguments, get_blocks,
        ),
        Tool(
            "get_waiting_funds",
            "백엔드가 이번 요청에 전달한 미배분 자금을 조회한다. 실제 자금을 이동하거나 분배하지 않는다.",
            EMPTY_PARAMETERS, no_arguments, get_waiting_funds,
        ),
        Tool(
            TRANSFER_TOOL_NAME,
            "사용자가 요청한 블록 간 이동 한 건의 제안 정보를 만든다. 먼저 get_blocks로 실제 ID를 확인한다. "
            "잔액 변경이나 제안 카드 저장은 하지 않는다. 백엔드가 사용자 확인 후 반영한다. "
            "이름·금액이 모호하거나 여러 이동을 한꺼번에 요청하면 먼저 질문한다.",
            TRANSFER_PARAMETERS, transfer_arguments, propose_block_transfer,
        ),
    ])
