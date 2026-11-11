"""Convert trusted, turn-local tool receipts to Spring's text/transfer contract."""
from copy import deepcopy
from dataclasses import replace

from pydantic import ValidationError

from app.agent.chat_session import ChatExecution
from app.modules.agent.contracts import AgentError
from app.schemas.coach_chat import CoachChatResponse, TransferProposal
from app.tools.fintris_chat import TRANSFER_TOOL_NAME

_REJECTION_TEXT = {
    "BLOCKS_NOT_PROVIDED": "현재 블록 정보를 받지 못해서 이동 제안을 만들 수 없어요. 블록 정보를 확인한 뒤 다시 요청해 주세요.",
    "BLOCK_NOT_FOUND": "선택한 블록을 현재 목록에서 찾지 못했어요. 어느 블록에서 어느 블록으로 옮길지 확인해 주세요.",
    "AMBIGUOUS_BLOCK_ID": "블록 식별 정보가 중복되어 이동 제안을 만들 수 없어요. 블록 정보를 다시 확인해 주세요.",
    "INVALID_ARGUMENTS": "이동 제안을 만들지 못했어요. 서로 다른 두 블록과 0원보다 큰 정수 금액을 확인해 주세요.",
}


def build_chat_response(execution: ChatExecution) -> CoachChatResponse:
    attempts = [item for item in execution.tool_executions if item.name == TRANSFER_TOOL_NAME]
    if not attempts:
        # Never parse transfer-like JSON from a model's free-form text or history.
        return CoachChatResponse(text=execution.result.message, transfer=None)

    # A later rejected attempt must not accidentally publish an older proposal.
    if attempts[-1].output.get("ok") is not True:
        code = attempts[-1].output.get("error")
        return CoachChatResponse(
            text=_REJECTION_TEXT.get(code, "이동 제안을 만들지 못했어요. 블록과 금액을 확인한 뒤 다시 요청해 주세요."),
            transfer=None,
        )

    candidates: dict[tuple[int, int, int], tuple[TransferProposal, dict]] = {}
    for item in attempts:
        if item.output.get("ok") is not True:
            continue  # An invalid attempt followed by a corrected one is allowed.
        try:
            data = item.output["data"]
            if (not isinstance(data, dict) or data.get("status") != "PROPOSAL_PREPARED"
                    or data.get("requiresUserConfirmation") is not True):
                raise ValueError("Invalid proposal receipt")
            proposal = TransferProposal.model_validate(data["transfer"])
            if not all(isinstance(data.get(k), str) and data[k].strip()
                       for k in ("fromBlockName", "toBlockName")):
                raise ValueError("Missing block names")
        except (KeyError, TypeError, ValueError, ValidationError):
            raise AgentError("INVALID_RESPONSE") from None
        key = (proposal.from_block_id, proposal.to_block_id, proposal.amount)
        candidates[key] = (proposal, data)

    # The backend contract carries ONE transfer. Never select a silent winner.
    # Identical repeated tool calls collapse to one candidate, not multiple writes.
    if len(candidates) != 1:
        return CoachChatResponse(
            text="한 번에 한 건의 이동만 제안할 수 있어요. 어느 블록에서 어느 블록으로 얼마를 먼저 옮길지 알려 주세요.",
            transfer=None,
        )
    proposal, data = next(iter(candidates.values()))
    # Server-owned wording cannot claim an unapproved change has already happened.
    return CoachChatResponse(
        text=(f"‘{data['fromBlockName']}’에서 ‘{data['toBlockName']}’으로 "
              f"{proposal.amount:,}원 이동하는 제안이에요. 변경 내용을 확인해 주세요. "
              "아직 잔액은 변경되지 않았어요."),
        transfer=proposal,
    )


def finalize_chat_execution(execution: ChatExecution) -> ChatExecution:
    """Host hook: store exactly the same assistant wording that the UI receives.

    This runs before the manager commits a successful turn. The generic agent
    and manager need no knowledge of blocks, proposals or Spring DTOs.
    """
    response = build_chat_response(execution)
    messages = deepcopy(execution.result.messages)
    if (messages and messages[-1].get("role") == "assistant"
            and not messages[-1].get("tool_calls")):
        messages[-1]["content"] = response.text
    else:
        messages.append({"role": "assistant", "content": response.text})
    return replace(execution, result=replace(
        execution.result, message=response.text, messages=messages
    ))
