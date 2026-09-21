"""Security controls.

Three layers, each independently testable:

1. ``injection``  - detects prompt-injection in user input *and* in retrieved documents
                    (indirect injection), classifying attempts as instruction override,
                    data exfiltration or tool abuse.
2. ``validation`` - schema/size validation of user requests, tool parameters and retrieved
                    content before anything reaches the LLM or a tool.
3. ``output_guard`` - post-generation guardrails: hallucinated citations, secret/PII leakage,
                    brand & compliance rules for a commercial bank, empty/invalid responses.

See docs/SECURITY.md for the threat model and rationale.
"""

from assistant.security.injection import InjectionVerdict, sanitize_retrieved_text, scan_prompt_injection
from assistant.security.output_guard import GuardReport, check_response
from assistant.security.validation import (
    ValidationError,
    validate_retrieved_chunk,
    validate_tool_params,
    validate_user_message,
)

__all__ = [
    "GuardReport",
    "InjectionVerdict",
    "ValidationError",
    "check_response",
    "sanitize_retrieved_text",
    "scan_prompt_injection",
    "validate_retrieved_chunk",
    "validate_tool_params",
    "validate_user_message",
]
