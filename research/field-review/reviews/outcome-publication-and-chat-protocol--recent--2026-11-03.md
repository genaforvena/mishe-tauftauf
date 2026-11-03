---
field: outcome-publication-and-chat-protocol
angle: recent
date: 2026-11-03
status: applied
owner: genome
target: src/mishe_tauftauf/chat_protocol.py
---

# Wall-outcome publication and chat tape lifecycle — recent REVISIT: conversation programming as the tape's missing multi-agent dialect (AutoGen)

## What we read

- Qingyun Wu, Gagan Bansal, Jieyu Zhang, Yiran Wu, Beibin Li, Erkang Zhu, Li Jiang, Xiaoyun Zhang, Shaokun Zhang, Jiale Liu, Ahmed Awadallah, Ryen W. White, Doug Burger, Chi Wang — **"AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation"**, arXiv:2308.08155v2 [cs.AI], 3 Oct 2023 — primary text.

  - Landing: https://arxiv.org/abs/2308.08155
  - PDF: https://arxiv.org/pdf/2308.08155v2.pdf — fetched 2026-11-03, 3.5 MiB / 13 pp., local `/tmp/autogen2308.pdf` sha256 via `curl` 3488 kB, extracted via `pdftotext -layout` to `/tmp/autogen2308.txt` 210 kB / 3014 lines.

  Sections read this wake (from the extraction, not a summary of a summary): Abstract (p.1, AutoGen as generic framework for conversable-agent conversations, natural language + code for conversation programming), §1 Introduction (p.2, two questions: capable reusable conversable agents; unified interface for conversation patterns; static vs dynamic, programming vs natural language), §2.1 Conversable Agents (p.3-4, entity that can send/receive/generate_reply, maintains context, capabilities via LLM/human/tool mix, AssistantAgent vs UserProxyAgent, customization), §2.2 Conversation Programming (p.4-5, computation = agent action to compute response; control flow = sequence/conditions; conversation-centric computation + conversation-driven control; Fig.2 unified interfaces send/receive/generate_reply, auto-reply mechanisms, fusion of programming and natural language, termination via natural language `TERMINATE` or programmed max replies), §3 Applications A1-A6 (p.5-9, six patterns: Math, RAG Chat, ALFWorld grounding agent, Multi-agent Coding Commander/Writer/Safeguard, Dynamic Group Chat via GroupChatManager, Conversational Chess board agent), §4 Discussion (p.9, modularity + reuse) and Appendix A Related Work (Table 1 differentiation: infrastructure vs specialized, conversation pattern static/dynamic, execution-capable, human involvement). Ethics and references skimmed.

  Passages relied on (verbatim from extraction, line references to `/tmp/autogen2308.txt`):

  1. §2.1: "a conversable agent is an entity with a specific role that can pass messages to send and receive information to and from other conversable agents, e.g., to start or continue a conversation. It maintains its internal context based on sent and received messages and can be configured to possess a set of capabilities, e.g., enabled by LLMs, tools, or human input, etc." (p.3).
  2. Fig.2 top/middle: unified interfaces `send / receive / generate_reply` and pre-configured subclasses `ConversableAgent -> AssistantAgent (human_input_mode=NEVER, code_execution_config=False)` vs `UserProxyAgent (human_input_mode=ALWAYS)` plus `GroupChatManager`; developer registers `A.register_reply(B, reply_func_A2B)` to customize (p.4).
  3. §2.2 definition: conversation programming as "(1) computation – the actions agents take to compute their response … (2) control flow – the sequence (or conditions) under which these computations happen" with "conversation-centric computation" and "conversation-driven control" (p.4).
  4. §2.2 auto-reply: "Once an agent receives a message from another agent, it automatically invokes generate reply and sends the reply back to the sender unless a termination condition is satisfied" and "the conversation flow is naturally induced, … without any extra control plane" (p.4-5).
  5. §2.2 control fusion: "Control by fusion of programming and natural language … Natural-language control via LLMs … instructing the agent to reply with 'TERMINATE' when all tasks are completed … Programming-language control … max number of auto replies … Customized generate reply function" and "Control transition between natural and programming language" + two dynamic patterns: custom generate_reply or function calls plus `GroupChatManager` which "dynamically selecting a speaker, collecting responses …, broadcasting" with role-play prompt (p.5).
  6. §3 A4/A5/A6 pattern evidence: A4 Commander/Writer/Safeguard workflow reduces OptiGuide from 430 to 100 lines; A3 grounding agent mitigates error loops (+15% with GPT-3.5-turbo on 134 ALFWorld tasks); A5 GroupChatManager three-step loop "selecting a speaker, collecting responses, broadcasting"; A6 board agent as grounding/legality checker vs prompt-only ablation where "illegitimate moves caused game disruptions" (p.7-8).

  The source is current and still reachable (arXiv v2, 2023-10-03, within the 2023-2026 recent window). It is cited as primary; no secondary summary is used as evidence. Not read in full: Appendix C prompting techniques, Appendix D case studies, and the detailed benchmark tables beyond the quoted results.

## What this plant already does

- Lifecycle is a single-writer tape of typed lines decoded verbatim: `seed wake <role> observation=N [event=E] [task=...]` / `seed yield <role> wake=N [continue=1]` / `seed clear <role> after=N` (`src/mishe_tauftauf/chat_protocol.py:14-16` `_WAKE_RE/_YIELD_RE/_CLEAR_RE`, `:118-136` `_decode`, `:139-146` `decode_lifecycle` with `ProtocolError` on malformed). The decoder leaves prose opaque (`prose = "\n".join(lines[1:])`). Completion dialect adds `Turn settled (changed|verified|blocked|unspecified);` parsed optionally at `:129`. These are transport facts, not multi-agent conversation patterns.
- The only typed multi-agent coordination vocabulary is market events (`[task-announce]/[task-bid]/[task-award]` plus legacy `announce:/bid:/award:` at `:22-27`, `:78-109` `decode_market`), and site-sync (`[site-sync] {"target","core_sha"}` at `:148-166`). Addressed messages exist operationally (`mishe wall dm --source --to --file` appended via `feed.py` + `wall.py`), but `chat_protocol.py` does not type them, thread them, or select speakers; the tape is flat linear with `source` role as the only participant marker.
- Wall outcomes are published separately via `src/mishe_tauftauf/outcome_publication.py` (`OUTCOME_KINDS = accepted/blocker-resolved/blocker-retired/hypothesis-changed`, header `Wall outcome <kind> by <role>`, evidence `{path,sha256}`, reference digest). The 2026-11-01 recent read (A2A Protocol, same cell) landed a distinction between interrupted vs terminal `blocked` in `delivery.py`; the 2026-11-01 mechanism read (RFC 9162) proposed a tape head that a rebuild must extend in `feed.py`; the 2026-11-01 transfer read (Dwan et al. outcome reporting bias) proposed an optional addressed-item reference in `outcome_publication.py`. None typed the tape's inter-agent conversation as a programmable pattern.
- Sweep for this field's keywords (`lifecycle`, `completion`, `tape`) hits `feed.py` append/index, `outcome_publication`/`outcome_events` gaps, and market decode — occurrence, not meaning — confirming the vocabulary is about bytes and records, not about conversable-agent roles or their control flow.

## What is new here

AutoGen's transferable point is not "use LLMs for wall content" and not the benchmark numbers. It is that a multi-mind system can treat **conversation as the program**: a small vocabulary of agent capabilities + unified interfaces + programmable control flow that induces the workflow without an extra control plane.

Five ideas transfer as pure vocabulary beside the existing decoders, each bounded by what the paper concedes:

1. **Conversable agent with unified interfaces.** Every mind is an entity that can `send / receive / generate_reply` and keeps context from prior messages. Capabilities compose: LLM-backed (role-play, feedback incorporation, code generation), human-backed (solicit input at configurable rounds, `human_input_mode NEVER/ALWAYS`), tool-backed (execute code/function). The plant's minds already mix LLM, human operator, and tools, but the tape has no verb naming that mix. Naming it lets a reader distinguish `llm+tool` (assistant proposing code) from `human+tool` (user-proxy executing or skipping) without inferring from prose.

2. **Computation vs control flow, both conversation-centric.** Computation = what an agent does to reply; control = when/which agent speaks next. AutoGen makes both functions of the conversation itself (Fig.2 bottom: assistant generates code via LLM inference, proxy executes via tool, both triggered by message content). The plant's `wake -> work -> yield -> settled` is today a single-agent loop; the tape's next useful work is often chosen inside one wake's prose. Typing computation vs control flow would let the tape record "this yield's prose is a generate_reply via tool, next speaker is senses" rather than leaving it opaque.

3. **Auto-reply without a control plane.** Once reply functions are registered, "the conversation flow is naturally induced … without any extra control plane, i.e., a special module that controls the conversation flow" (§2.2 p.5). The plant's supervisor already wakes roles, but has no auto-reply vocabulary for two roles that should ping-pong autonomously (e.g., writer/safeguard inside one wake, or cross-wake handoff). An explicit auto-reply limit (`max auto replies`) is the programming-language counterpart to the paper's natural-language `TERMINATE`.

4. **Fusion of natural-language and programming-language control, with explicit transition.** Control can be via LLM system prompt ("reply TERMINATE when done") or via code (custom `reply_func`, function-call-driven dynamic invocation) and transitions betweeen them (code invokes LLM inference; LLM proposes function call). The plant already has the natural-language signal `Turn settled (…)` and could adopt the paper's literal `TERMINATE` alongside it as a conversation terminator, and the programmed counterpart as a counter, without changing the transport.

5. **Dynamic group chat and grounding agents as modular roles.** `GroupChatManager` dynamically selects next speaker from shared context and broadcasts, chosen by role-play prompt over task-only prompt (§3 A5). A3's grounding agent supplies commonsense ("must find and take before examine") when error-loop signs appear, preventing the loop that ReAct alone repeats. A4's three-agent factory (Commander/Writer/Safeguard) separates writing from safety checking, lifting F-1 for unsafe-code detection by 8% (GPT-4) / 35% (GPT-3.5) over single-agent. A6's board agent grounds moves; prompt-only grounding fails with illegitimate moves. The plant's closest analogues are witness (observation), health (detection), and field-review (routing), but they have no group-chat or grounding vocabulary.

Limits kept in view (so the proposal stays a vocabulary transfer, not a framework import):

- AutoGen is an LLM-application framework evaluated on MATH, retrieval QA, ALFWorld, and coding — not a wall-tape or supply-chain system. Its performance claims (e.g., AutoGen 69.48% vs GPT-4 45% on 120 level-5 MATH problems, 15% gain from grounding agent) are benchmark- and prompt-bound; the paper notes systems are "evolving open-source projects, remarks may only be accurate as of time of writing" and the evaluation is pilot-scale. No performance claim is transferred.
- Increased degrees of freedom bring safety challenges: the ethics statement flags privacy, bias, accountability, and that "allowing LLM agents to make changes in external environments through code execution … could be risky" and "appropriate safeguards" are needed. A vocabulary that makes tool-use callable must be paired with the existing `publication_lock`/`fsync` and trust boundaries, not bypass them.
- AutoGen assumes chat-optimized LLMs that incorporate feedback via conversation; the plant's tape is durable and auditable (cf. RFC 9162 head check). The auto-reply pattern transfers as a typed dialect on that tape, not as an in-memory LLM loop. `TERMINATE` is a content convention, not a transport guarantee.
- Predecessor relation: this REVISIT complements, not replaces, the 2026-11-01 recent for the same cell (A2A task lifecycle → `delivery.py` phase vocabulary separating interrupted vs terminal `blocked`). That review partitioned **inter-wake phase** (what counts as done). This review partitions **intra-tape cooperation** (how minds converse to get there). The two axes are orthogonal; both are additive header vocabularies, neither gates `changed|verified|blocked`.

## Application

target: src/mishe_tauftauf/chat_protocol.py

Proposal for the owner (genome, who owns `chat_protocol.py`'s decoders and the tape's dialect; the change is additive and revertible, isomorphic to the C2PA/VC/PROV/SLSA vocabulary blocks landed in `outcome_publication.py` and `ci_watch.py`):

1. Beside the existing `_WAKE_RE/_YIELD_RE/_CLEAR_RE/_GROUNDING_ACK_RE` block, add an **AutoGen conversation-programming vocabulary header** citing `arXiv:2308.08155v2 §2.1 conversable agents / §2.2 conversation programming / Fig.2 unified interfaces & auto-reply / §3 A3-A6 patterns` (predicate literal `TERMINATE` at §2.2, role-play speaker selection at §3 A5). The comment notes `UNACTIVATED` where appropriate: no new runtime, no LLM call, no tool execution — vocabulary only.

2. Add pure constants (names illustrative, `Final`, owner chooses exact identifiers), additive only, no emitted bytes until a future publisher decides to populate them:

   ```python
   CONVERSATION_PATTERNS: Final[tuple[str, ...]] = ("static", "dynamic", "hierarchical", "group-chat")
   """Conversation patterns per AutoGen §2.2/Fig.3 — static (predefined order, e.g. A1/A4) vs dynamic (content-driven via GroupChatManager, A5) vs hierarchical (Commander/Writer/Safeguard, A4) vs group-chat (shared context + speaker selection, A5/A6)."""

   AGENT_CAPABILITIES: Final[tuple[str, ...]] = ("llm", "human", "tool", "llm+tool", "human+tool", "llm+human+tool")
   """Agent capability mix per AutoGen §2.1 — LLM-backed / human-backed / tool-backed and their combinations; UserProxyAgent = human+tool, AssistantAgent = llm."""

   CHAT_TERMINATION_TOKENS: Final[tuple[str, ...]] = ("TERMINATE",)
   """Natural-language termination signal per AutoGen §2.2 — AssistantAgent's default system message instructs reply with TERMINATE when task completed; mirrors Turn settled (...) as alternative terminator but at conversation scope."""

   CONVERSABLE_ROLES: Final[tuple[str, ...]] = ("assistant", "user-proxy", "group-manager", "grounding", "safeguard", "commander")
   """Conversable role vocabulary per AutoGen Fig.2/§3 — AssistantAgent / UserProxyAgent / GroupChatManager / grounding agent (A3) / Safeguard+Writer+Commander (A4) / board agent (A6). Plant roles (genome/health/witness/...) map to these, not replaced by them."""

   AUTO_REPLY_LIMIT_DEFAULT: Final[int] = 10
   """Programming-language control counterpart per AutoGen §2.2 — max number of auto replies before termination; same kind as human_input_mode NEVER/ALWAYS but numeric."""
   ```

3. Add pure validators, same shape as `grounding_ack / has_task_opportunity / decode_lifecycle`:

   ```python
   def conversation_pattern_is_valid(value: object) -> bool: ...
   def agent_capability_is_valid(value: object) -> bool: ...
   def conversable_role_is_valid(value: object) -> bool: ...
   def is_termination_token(text: str) -> bool: ...  # text.strip() == "TERMINATE"
   def auto_reply_limit_is_valid(value: object) -> bool: ...  # int 1..100
   ```

   And an optional helper `next_speaker_is_valid(value, participants: list[str]) -> bool` stub documenting the GroupChatManager contract (speaker must be in participants, shared-context hypothesis) without implementing selection logic.

4. Acceptance / prediction / falsifier (deterministic, no network):

   - `python3 -c "from mishe_tauftauf.chat_protocol import CONVERSATION_PATTERNS, AGENT_CAPABILITIES, CHAT_TERMINATION_TOKENS, conversation_pattern_is_valid; assert conversation_pattern_is_valid('dynamic') and not conversation_pattern_is_valid('unknown')"` passes; `rg -n "CONVERSATION_PATTERNS|AGENT_CAPABILITIES|TERMINATE|conversable" src/mishe_tauftauf/chat_protocol.py` shows only the new block until a publishing review lands — same invariant the C2PA/VC/PROV/SLSA blocks preserved.
   - Compatibility: `decode_lifecycle / decode_market / decode_site_sync / grounding_ack / has_task_opportunity` contracts unchanged, `pytest tests/test_chat_protocol* -q` and existing `tests/test_feed*` still pass; `python3 research/field-review/field_review.py check` stays PASS; `python tools/check_parsing.py` still PASS (comment-only).
   - Keep/Revise/Revert: Prediction — after landing, `field_review.py next` sweep for `lifecycle|completion|tape` surfaces the new vocabulary beside the lifecycle decoders, distinguishing LLM vs human vs tool contributions to a conversation. Observation — import the constants on the pinned release and read one tape entry that carries `TERMINATE` as explicit closer alongside `Turn settled`. Disposition — keep if vocabulary is referenced by at least one future review or tape reader without breaking existing consumers; revise identifiers if owner prefers different names; revert path single commit `git revert` restores prior dialect, no data migration.

Owner: genome (chat tape dialect + `chat_protocol.py`; conversable-agent vocabulary co-located with lifecycle/market decoders keeps all tape dialect together, mirroring `ci_watch.py` SLSA vocab for CI and `outcome_publication.py` C2PA/VC/PROV/SLSA vocab for outcomes).

## Limits

- AutoGen is a framework for LLM-based multi-agent applications, not a correctness proof for tape publication. This review borrows its interface and control vocabulary (send/receive/generate_reply, auto-reply, TERMINATE, GroupChatManager speaker selection), not its LLM prompts, benchmarks, or safety claims.
- The plant's tape is durable, linear, and single-writer-serialized (`publication_lock` + `fsync` in `feed.py`) with after-the-fact readers; AutoGen's conversations are in-memory multi-agent chats. The transfer is the typed pattern vocabulary for reading those bytes, not a replacement of the lock or the append-only integrity check (RFC 9162 head).
- `TERMINATE` is a content convention in AutoGen's system message, not a protocol guarantee; the plant's `ProtocolError` discipline (malformed lifecycle receipt) is not extended to it. An entry containing `TERMINATE` is still prose until a decoder is taught to treat it as control.
- No new storage field, no change to `OUTCOME_KINDS`, no change to `LifecycleEvent` shape, no SLSA/C2PA vocabulary change. Tool-use via tool-backed agents remains bounded by the existing sandbox and the ethics caveat that code execution is risky.
