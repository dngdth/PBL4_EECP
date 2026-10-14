from enum import StrEnum


class ErrorCode(StrEnum):
    UNSUPPORTED_PROTOCOL_VERSION = "UNSUPPORTED_PROTOCOL_VERSION"
    INVALID_MESSAGE = "INVALID_MESSAGE"
    INVALID_COMMAND = "INVALID_COMMAND"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    POLICY_HASH_MISMATCH = "POLICY_HASH_MISMATCH"
    POLICY_EXPIRED = "POLICY_EXPIRED"
    SESSION_MISMATCH = "SESSION_MISMATCH"
    TARGET_MISMATCH = "TARGET_MISMATCH"
    INVALID_POLICY = "INVALID_POLICY"
    COMMAND_EXPIRED = "COMMAND_EXPIRED"
    DUPLICATE_COMMAND = "DUPLICATE_COMMAND"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


ERROR_DESCRIPTIONS: dict[ErrorCode, str] = {
    ErrorCode.UNSUPPORTED_PROTOCOL_VERSION: "The protocol major version is unsupported.",
    ErrorCode.INVALID_MESSAGE: "The message is malformed or fails contract validation.",
    ErrorCode.INVALID_COMMAND: "The command fields or payload are invalid.",
    ErrorCode.UNSUPPORTED_OPERATION: "The requested operation is not allowlisted.",
    ErrorCode.POLICY_HASH_MISMATCH: "The policy content does not match its declared hash.",
    ErrorCode.POLICY_EXPIRED: "The issued policy is no longer valid.",
    ErrorCode.SESSION_MISMATCH: "The message does not belong to the expected session.",
    ErrorCode.TARGET_MISMATCH: "The message target does not match the consumer.",
    ErrorCode.INVALID_POLICY: "The policy shape or values are invalid.",
    ErrorCode.COMMAND_EXPIRED: "The command deadline has passed.",
    ErrorCode.DUPLICATE_COMMAND: "The command identity was already processed.",
    ErrorCode.EXECUTION_FAILED: "The allowlisted operation failed during execution.",
    ErrorCode.INTERNAL_ERROR: "An unexpected internal error prevented processing.",
}
