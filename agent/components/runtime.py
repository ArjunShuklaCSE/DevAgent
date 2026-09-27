"""How components talk to the model: layered prompts, structured answers, tool loops.

``ComponentRuntime`` is shared by all components of one run. It loads each component's
versioned prompt (and records the version), builds requests with the untrusted-content
wrapping from ``llm.prompting``, and runs the bounded tool loop that the localizer,
reproducer and editor use. Tool output returned to the model is wrapped as untrusted
data, and text the agent reads is scanned for injection attempts.
"""

import json
from dataclasses import dataclass, field

import structlog
from pydantic import ValidationError

from llm.client import LLMClient
from llm.injection import InjectionFlag, scan
from llm.prompting import Prompt, PromptLibrary, UntrustedContent, build_system, build_user_turn
from llm.structured import (
    SUBMIT_TOOL,
    StructuredOutput,
    StructuredOutputError,
    complete_structured,
    submit_tool,
)
from llm.types import (
    ContentBlock,
    LLMRequest,
    Message,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from tools.base import ToolContext
from tools.registry import ToolRegistry, ToolResult

logger = structlog.get_logger(__name__)

# Tools whose output is repository text worth scanning for injected instructions.
_SCANNED_TOOLS = frozenset({"read_file", "search_text"})
MAX_INVALID_SUBMITS = 3


class ToolLoopExhaustedError(Exception):
    """The model used all its tool rounds without submitting a valid answer."""

    def __init__(self, component: str, rounds: int) -> None:
        super().__init__(f"{component} did not submit an answer within {rounds} tool rounds")
        self.component = component
        self.rounds = rounds


@dataclass(frozen=True)
class ModelSettings:
    model: str
    temperature: float = 0.0
    max_output_tokens: int = 4096


@dataclass
class ToolLoopResult[T: StructuredOutput]:
    value: T
    rounds: int
    tool_results: list[ToolResult] = field(default_factory=list)


class ComponentRuntime:
    def __init__(
        self,
        client: LLMClient,
        settings: ModelSettings,
        prompts: PromptLibrary,
        registry: ToolRegistry,
    ) -> None:
        self._client = client
        self._settings = settings
        self._prompts = prompts
        self._registry = registry
        self._loaded: dict[str, Prompt] = {}
        self.injection_flags: list[InjectionFlag] = []
        self._flag_keys: set[tuple[str, str, int]] = set()

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def prompt_versions(self) -> dict[str, str]:
        return {name: prompt.version_id for name, prompt in sorted(self._loaded.items())}

    def prompt(self, component: str) -> Prompt:
        if component not in self._loaded:
            self._loaded[component] = self._prompts.load(component)
        return self._loaded[component]

    def flag(self, text: str, source: str) -> list[InjectionFlag]:
        """Scan ``text`` and keep new flags (deduplicated by pattern, source and line)."""
        new = []
        for item in scan(text, source):
            key = (item.pattern_id, item.source, item.line)
            if key not in self._flag_keys:
                self._flag_keys.add(key)
                self.injection_flags.append(item)
                new.append(item)
        return new

    def _request(
        self,
        component: str,
        messages: list[Message],
        tools: list[ToolDefinition],
        tool_choice: str,
        *,
        step_id: str | None,
        attempt: int = 1,
    ) -> LLMRequest:
        prompt = self.prompt(component)
        metadata = {
            "component": component,
            "prompt_version": prompt.version_id,
            "attempt": str(attempt),
        }
        if step_id is not None:
            metadata["step_id"] = step_id
        return LLMRequest(
            model=self._settings.model,
            system=build_system(prompt),
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=self._settings.max_output_tokens,
            temperature=self._settings.temperature,
            metadata=metadata,
        )

    async def structured[T: StructuredOutput](
        self,
        component: str,
        task: str,
        untrusted: list[UntrustedContent],
        schema: type[T],
        *,
        step_id: str | None,
    ) -> T:
        """One structured answer, no tools (validated, retried up to 3 attempts)."""
        request = self._request(
            component,
            [Message.user(build_user_turn(task, untrusted))],
            [],
            SUBMIT_TOOL,
            step_id=step_id,
        )
        result = await complete_structured(self._client, request, schema)
        return result.value

    async def tool_loop[T: StructuredOutput](
        self,
        component: str,
        task: str,
        untrusted: list[UntrustedContent],
        schema: type[T],
        *,
        tools: list[str],
        ctx: ToolContext,
        max_rounds: int,
    ) -> ToolLoopResult[T]:
        """Let the model use ``tools`` until it submits a valid ``schema`` answer.

        Each round is one LLM call; every tool call in it is executed through the
        registry (so it is policy-checked and logged). Invalid submissions get the
        validation errors back. After ``max_rounds`` the model gets one last turn in
        which it can only submit.
        """
        allowed = set(tools)
        definitions = [
            ToolDefinition(
                name=spec["name"],
                description=spec["description"],
                input_schema=spec["input_schema"],
            )
            for spec in self._registry.specs()
            if spec["name"] in allowed
        ]
        submit = submit_tool(schema, "Submit your final answer for this step.")
        messages = [Message.user(build_user_turn(task, untrusted))]
        executed: list[ToolResult] = []
        invalid = 0
        for round_number in range(1, max_rounds + 2):
            final_round = round_number > max_rounds
            if final_round:
                messages = _add_user_text(
                    messages, "You have used all tool rounds. Submit your answer now."
                )
            request = self._request(
                component,
                messages,
                [submit] if final_round else [*definitions, submit],
                SUBMIT_TOOL if final_round else "any",
                step_id=ctx.step_id,
                attempt=round_number,
            )
            response = await self._client.complete(request)
            messages.append(_assistant(response.content))
            uses = response.tool_uses
            if not uses:
                messages.append(Message.user("Use one of the tools, or call submit to finish."))
                continue
            results: list[ContentBlock] = []
            for use in uses:
                if use.name == SUBMIT_TOOL:
                    try:
                        value = schema.model_validate(use.input)
                    except ValidationError as exc:
                        invalid += 1
                        if invalid >= MAX_INVALID_SUBMITS:
                            raise StructuredOutputError(invalid, _errors(exc)) from exc
                        results.append(_error_result(use, f"Invalid answer:\n{_errors(exc)}"))
                        continue
                    return ToolLoopResult(value=value, rounds=round_number, tool_results=executed)
                results.append(await self._call_tool(use, allowed, ctx, executed))
            messages.append(Message(role="user", content=results))
        raise ToolLoopExhaustedError(component, max_rounds)

    async def _call_tool(
        self, use: ToolUseBlock, allowed: set[str], ctx: ToolContext, executed: list[ToolResult]
    ) -> ToolResultBlock:
        if use.name not in allowed:
            return _error_result(
                use, f"Tool {use.name!r} is not available in this step. Tools: {sorted(allowed)}"
            )
        result = await self._registry.call(use.name, use.input, ctx)
        executed.append(result)
        if use.name in _SCANNED_TOOLS and result.ok:
            source = str(use.input.get("path") or use.input.get("pattern") or use.name)
            self.flag(result.output, f"{use.name}:{source}"[:200])
        attributes: dict[str, str] = {"tool": use.name, "status": result.status.value}
        wrapped = UntrustedContent(
            content=result.output, source="tool_output", attributes=tuple(attributes.items())
        ).render()
        return ToolResultBlock(tool_use_id=use.id, content=wrapped, is_error=not result.ok)


def _assistant(content: list[ContentBlock]) -> Message:
    return Message(role="assistant", content=list(content) or [TextBlock(text="(no content)")])


def _add_user_text(messages: list[Message], text: str) -> list[Message]:
    """Append ``text`` to the last user turn (after any tool results) or add one."""
    last = messages[-1]
    if last.role == "user":
        return [*messages[:-1], Message(role="user", content=[*last.content, TextBlock(text=text)])]
    return [*messages, Message.user(text)]


def _error_result(use: ToolUseBlock, message: str) -> ToolResultBlock:
    return ToolResultBlock(tool_use_id=use.id, content=message, is_error=True)


def _errors(exc: ValidationError) -> str:
    return "\n".join(
        f"- {'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
        for err in exc.errors(include_url=False)
    )[:2000]


def compact_json(value: object) -> str:
    return json.dumps(value, indent=1, sort_keys=True, default=str)
