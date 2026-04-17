from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional


class BlueprintWorkflowStage(str, Enum):
    PARSE = "parse"
    VALIDATE = "validate"
    EXECUTE = "execute"
    VERIFY = "verify"
    REPORT = "report"


class BlueprintWorkflowFailure(str, Enum):
    PARSE_FAILURE = "parse_failure"
    VALIDATION_FAILURE = "validation_failure"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    UNSUPPORTED = "unsupported"
    EXECUTION_FAILURE = "execution_failure"
    VERIFICATION_FAILURE = "verification_failure"


_VALIDATION_ERROR_CODES = {"invalid_blueprint_path", "invalid_name", "duplicate_name"}
_VERIFICATION_ERROR_CODES = {"compile_failed", "operation_failed"}
_RUNTIME_UNAVAILABLE_ERROR_CODES = {"unreal_runtime_unavailable", "runtime_unavailable"}
_UNSUPPORTED_ERROR_CODES = {"unsupported", "not_supported", "capability_unavailable", "editor_only"}


def _stage(
    name: BlueprintWorkflowStage,
    status: str,
    reason: str,
    code: Optional[str] = None,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "stage": str(name.value),
        "status": str(status),
        "reason": str(reason),
    }
    if code:
        entry["code"] = str(code)
    return entry


def _report_workflow(
    *,
    action: str,
    ok: bool,
    failure_type: Optional[BlueprintWorkflowFailure],
    failed_stage: Optional[BlueprintWorkflowStage],
    stages: list[Dict[str, Any]],
    execution_error_code: Optional[str] = None,
    normalized_error_code: Optional[str] = None,
    raw_error_code: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "workflow": "blueprint_action",
        "action": str(action or "unknown"),
        "ok": bool(ok),
        "failure_type": str(failure_type.value) if failure_type is not None else None,
        "failed_stage": str(failed_stage.value) if failed_stage is not None else None,
        "execution_error_code": str(execution_error_code or "").strip() or None,
        "normalized_error_code": str(normalized_error_code or "").strip() or None,
        "raw_error_code": str(raw_error_code or "").strip() or None,
        "stages": stages,
    }
    if details:
        payload["details"] = dict(details)
    return payload


def parse_failure_workflow(action: str, reason: str) -> Dict[str, Any]:
    return _report_workflow(
        action=action,
        ok=False,
        failure_type=BlueprintWorkflowFailure.PARSE_FAILURE,
        failed_stage=BlueprintWorkflowStage.PARSE,
        execution_error_code="parse_failure",
        normalized_error_code="parse_failure",
        raw_error_code="parse_failure",
        stages=[
            _stage(BlueprintWorkflowStage.PARSE, "failed", reason, "parse_failure"),
            _stage(BlueprintWorkflowStage.VALIDATE, "skipped", "Skipped because parse failed."),
            _stage(BlueprintWorkflowStage.EXECUTE, "skipped", "Skipped because parse failed."),
            _stage(BlueprintWorkflowStage.VERIFY, "skipped", "Skipped because parse failed."),
            _stage(BlueprintWorkflowStage.REPORT, "success", "Failure reported."),
        ],
    )


def validation_failure_workflow(
    action: str,
    reason: str,
    code: str = "validation_failure",
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _report_workflow(
        action=action,
        ok=False,
        failure_type=BlueprintWorkflowFailure.VALIDATION_FAILURE,
        failed_stage=BlueprintWorkflowStage.VALIDATE,
        execution_error_code=code,
        normalized_error_code=code,
        raw_error_code=code,
        details=details,
        stages=[
            _stage(BlueprintWorkflowStage.PARSE, "success", "Blueprint command parsed."),
            _stage(BlueprintWorkflowStage.VALIDATE, "failed", reason, code),
            _stage(BlueprintWorkflowStage.EXECUTE, "skipped", "Skipped because validation failed."),
            _stage(BlueprintWorkflowStage.VERIFY, "skipped", "Skipped because validation failed."),
            _stage(BlueprintWorkflowStage.REPORT, "success", "Failure reported."),
        ],
    )


def success_workflow(action: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _report_workflow(
        action=action,
        ok=True,
        failure_type=None,
        failed_stage=None,
        details=details,
        stages=[
            _stage(BlueprintWorkflowStage.PARSE, "success", "Blueprint command parsed."),
            _stage(BlueprintWorkflowStage.VALIDATE, "success", "Command payload validated."),
            _stage(BlueprintWorkflowStage.EXECUTE, "success", "Blueprint operation executed."),
            _stage(BlueprintWorkflowStage.VERIFY, "success", "Blueprint state verification succeeded."),
            _stage(BlueprintWorkflowStage.REPORT, "success", "Result reported."),
        ],
    )


def normalize_execution_error_code(error_code: str, message: str) -> str:
    raw_code = str(error_code or "").strip().lower()
    normalized_message = str(message or "").strip().lower()

    if raw_code in _RUNTIME_UNAVAILABLE_ERROR_CODES:
        return "runtime_unavailable"
    if raw_code in _VALIDATION_ERROR_CODES:
        return raw_code
    if raw_code in _VERIFICATION_ERROR_CODES:
        return raw_code
    if raw_code in _UNSUPPORTED_ERROR_CODES:
        return "unsupported"

    if (
        "runtime unavailable" in normalized_message
        or "runtime is unavailable" in normalized_message
        or ("unreal runtime" in normalized_message and "unavailable" in normalized_message)
    ):
        return "runtime_unavailable"
    if "unsupported" in normalized_message or "editor-only" in normalized_message:
        return "unsupported"
    if "compile failed" in normalized_message:
        return "compile_failed"

    return "execution_failure"


def classify_execution_failure(error_code: str, message: str) -> tuple[BlueprintWorkflowFailure, BlueprintWorkflowStage, str]:
    normalized_code = normalize_execution_error_code(error_code, message)

    if normalized_code == "runtime_unavailable":
        return BlueprintWorkflowFailure.RUNTIME_UNAVAILABLE, BlueprintWorkflowStage.EXECUTE, normalized_code
    if normalized_code in _VALIDATION_ERROR_CODES:
        return BlueprintWorkflowFailure.VALIDATION_FAILURE, BlueprintWorkflowStage.VALIDATE, normalized_code
    if normalized_code in _VERIFICATION_ERROR_CODES:
        return BlueprintWorkflowFailure.VERIFICATION_FAILURE, BlueprintWorkflowStage.VERIFY, normalized_code
    if normalized_code == "unsupported":
        return BlueprintWorkflowFailure.UNSUPPORTED, BlueprintWorkflowStage.EXECUTE, normalized_code

    return BlueprintWorkflowFailure.EXECUTION_FAILURE, BlueprintWorkflowStage.EXECUTE, normalized_code


def failed_execution_workflow(
    action: str,
    *,
    error_code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    failure_type, failed_stage, normalized_code = classify_execution_failure(error_code, message)
    raw_code = str(error_code or "").strip().lower()
    failure_code = raw_code or normalized_code or str(failure_type.value)

    parse_stage = _stage(BlueprintWorkflowStage.PARSE, "success", "Blueprint command parsed.")
    validate_stage = _stage(BlueprintWorkflowStage.VALIDATE, "success", "Command payload validated.")
    execute_stage = _stage(BlueprintWorkflowStage.EXECUTE, "success", "Execution attempted.")
    verify_stage = _stage(BlueprintWorkflowStage.VERIFY, "skipped", "Verification was not reached.")

    if failure_type == BlueprintWorkflowFailure.RUNTIME_UNAVAILABLE:
        execute_stage = _stage(
            BlueprintWorkflowStage.EXECUTE,
            "failed",
            "Runtime is unavailable for Unreal-dependent operation.",
            normalized_code,
        )
        verify_stage = _stage(
            BlueprintWorkflowStage.VERIFY,
            "skipped",
            "Skipped because runtime was unavailable.",
        )
    elif failure_type == BlueprintWorkflowFailure.VALIDATION_FAILURE:
        validate_stage = _stage(
            BlueprintWorkflowStage.VALIDATE,
            "failed",
            "Validation failed while resolving blueprint operation constraints.",
            normalized_code,
        )
        execute_stage = _stage(
            BlueprintWorkflowStage.EXECUTE,
            "skipped",
            "Skipped because validation failed.",
        )
        verify_stage = _stage(
            BlueprintWorkflowStage.VERIFY,
            "skipped",
            "Skipped because validation failed.",
        )
    elif failure_type == BlueprintWorkflowFailure.VERIFICATION_FAILURE:
        execute_stage = _stage(
            BlueprintWorkflowStage.EXECUTE,
            "success",
            "Execution completed but requires verification pass.",
        )
        verify_stage = _stage(
            BlueprintWorkflowStage.VERIFY,
            "failed",
            "Verification after execution failed.",
            normalized_code,
        )
    elif failure_type == BlueprintWorkflowFailure.UNSUPPORTED:
        execute_stage = _stage(
            BlueprintWorkflowStage.EXECUTE,
            "failed",
            "Requested blueprint operation is unsupported in current runtime capabilities.",
            normalized_code,
        )
        verify_stage = _stage(
            BlueprintWorkflowStage.VERIFY,
            "skipped",
            "Skipped because capability is unsupported.",
        )
    else:
        execute_stage = _stage(
            BlueprintWorkflowStage.EXECUTE,
            "failed",
            "Execution failed before verification completed.",
            normalized_code,
        )
        verify_stage = _stage(
            BlueprintWorkflowStage.VERIFY,
            "skipped",
            "Skipped because execution failed.",
        )

    return _report_workflow(
        action=action,
        ok=False,
        failure_type=failure_type,
        failed_stage=failed_stage,
        execution_error_code=failure_code,
        normalized_error_code=normalized_code,
        raw_error_code=raw_code,
        details=details,
        stages=[
            parse_stage,
            validate_stage,
            execute_stage,
            verify_stage,
            _stage(BlueprintWorkflowStage.REPORT, "success", "Failure reported."),
        ],
    )
