# 0014. LLM layer: provider-neutral client, metering, budgets, structured outputs

- Status: Accepted (Phase 5)
- Date: 2026-09-27

## Context
Spec 3, 6.6, 7 and 14 ask for:
- a provider-agnostic client with native tool calling;
- cost accounting from config;
- budgets enforced before every action;
- versioned, layered prompts with untrusted content wrapped;
- structured outputs validated with bounded retries;
- tests that run without API keys.

## Decision
- **Neutral types, thin adapters** (`llm/types.py`). Agent code builds `LLMRequest`s of
  neutral messages, content blocks and tool definitions. The two adapters,
  `AnthropicClient` (Messages API) and `OpenAIClient` (Chat Completions), are plain
  `httpx` calls. We don't use the provider SDKs: the wire format is small, fully
  covered by mocked-transport tests, and we keep control of retries (bounded
  exponential backoff on 408/409/429/5xx/529, no retry on 4xx auth or validation
  errors) and of what gets logged. Request `metadata` (component, prompt version,
  attempt, step) is never sent to a provider.
- **Model names and prices are configuration.** `config/model_pricing.yaml` maps each
  model to a provider and per-million-token prices, with `as_of` and `source`. The
  entry's provider selects the adapter. A model with no price entry is refused
  (`unpriced_model`), because its cost budget could not be enforced. The shipped file
  holds a few OpenAI entries with their published prices and date. Operators add the
  models they use.
- **Metering wraps any client** (`MeteredLLMClient`). Every call is priced, checked
  against the run budget before it is sent, and recorded through an `LlmCallSink`,
  including provider errors and calls refused by the budget. The backend's
  `DbLlmCallSink` writes `llm_calls` and emits `llm_usage` events.
- **Budgets** (`llm/budget.py`):
  - one `BudgetTracker` per run, with steps, fix attempts, tokens, cost and wall clock;
  - `before_step` and `before_fix_attempt` refuse the action that would exceed a limit;
  - `before_llm_call` is **worst case**: it adds the estimated input tokens (4
    characters per token) and the full `max_tokens` output allowance, and refuses the
    call if that could cross the token or cost limit. Spending can therefore not
    overshoot a limit by one call's worth, at the price of stopping slightly early;
  - the orchestrator turns `BudgetExceededError` into the `budget_exceeded` status with
    its message (Phase 6).
- **Prompt layers** (`llm/prompting.py`):
  - the system prompt holds the fixed policy (untrusted tags are data; the tools are
    the only capabilities; minimal changes; a short rationale, no hidden reasoning),
    followed by the component's role prompt;
  - the user turn holds the task, followed by untrusted content;
  - untrusted content is wrapped as
    `<untrusted source=... path=... lines=...>`. Any `<untrusted` or `</untrusted`
    inside the content is escaped, so data cannot close its wrapper, and attribute
    values are sanitised;
  - role prompts live in `agent/prompts/<component>/vN.md`. The version id stored on
    every call is `vN:<sha256 prefix>`, so an edited file can never pass for the old
    version.
- **Structured outputs** (`llm/structured.py`):
  - the model must answer by calling one `submit` tool whose schema is the output
    model's JSON schema;
  - the input is validated with Pydantic (`extra="forbid"`). On failure, the errors go
    back as an error tool result and the model tries again, up to 3 attempts. Each
    attempt is its own logged call;
  - every output model extends `StructuredOutput`, which requires a 1–3 sentence
    `rationale`. The rationale is stored with the call;
  - using tool calls rather than asking for JSON in text works the same way across
    both providers.
- **Injection heuristics** (`llm/injection.py`) flag passages that try to override
  instructions, hijack the role, exfiltrate secrets, edit CI, add dependencies, call
  out to the network, or change git remotes. They feed a warning badge. They are not a
  control.
- **`ScriptedLLM`** replays scripted steps, or YAML cassettes. Each step can assert the
  component, required prompt text and tool choice of the request it answers, so a
  scripted run fails loudly if the orchestrator's flow changes.

## Consequences
- The repository ships no Anthropic model entries. To use that adapter, add your model
  ID and its current prices to `config/model_pricing.yaml` and set
  `DEVAGENT_LLM_MODEL` and `DEVAGENT_ANTHROPIC_API_KEY`.
- No real provider call has been made from this environment, because no key is
  available. The adapters are verified against the documented wire formats with
  mocked HTTP. A live smoke test is part of Phase 6's manual demo, once a key exists.
- The worst-case budget check can refuse a final call that would in fact have fitted.
  That is intended.
