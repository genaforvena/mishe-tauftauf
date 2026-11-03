"""Boundary adapters for lifecycle and linked-site completion records.

Machine control records are decoded here; explanatory prose remains opaque.

AutoGen conversation-programming vocabulary (Wu et al. arXiv:2308.08155v2
§2.1 conversable agents / §2.2 conversation programming / Fig.2 unified
interfaces send/receive/generate_reply & auto-reply without control plane /
§3 A3 grounding agent / A4 Commander-Writer-Safeguard / A5 GroupChatManager
/ A6 board agent) — additive header, no new runtime. Predicate literal
``TERMINATE`` at §2.2 (natural-language termination) and role-play speaker
selection at §3 A5 are UNACTIVATED: no LLM call, no tool execution, no
emitted bytes until a future publisher populates them. Limits: benchmark
numbers and prompt text not transferred; code-execution safety caveat
retained; ``TERMINATE`` is content convention until a decoder treats it as
control.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from .feed import FeedEntry, FeedError

_WAKE_RE = re.compile(r"seed wake ([a-z0-9-]+) observation=([1-9][0-9]*)(?: event=([1-9][0-9]*))?(?: task=(\S+))?\Z")
_YIELD_RE = re.compile(r"seed yield ([a-z0-9-]+) wake=([1-9][0-9]*)( continue=1)?\Z")
_CLEAR_RE = re.compile(r"seed clear ([a-z0-9-]+) after=([1-9][0-9]*)\Z")
_RESERVED = re.compile(r"\s*seed (?:wake|yield|clear)(?:\s|\Z)")
_SETTLED = re.compile(r"Turn settled \(([a-z]+)\);")
_SITE_SYNC_HEADER = "[site-sync] "
_CORE_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
_KERNEL_SYNC_TASK_RE = re.compile(r"kernel-sync-[0-9a-f]{12}-(.+)\Z")
_MARKET_TOKEN = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_MARKET_LEGACY = {
    "announce": re.compile(r"announce:(?P<identity>[A-Za-z0-9._:-]{1,128}):requires=(?P<qual>[A-Za-z0-9._:-]{1,64})\Z"),
    "bid": re.compile(r"bid:(?P<identity>[A-Za-z0-9._:-]{1,128}):qual=(?P<qual>[A-Za-z0-9._:-]{1,64}):available=(?P<number>\d+)\Z"),
    "award": re.compile(r"award:(?P<identity>[A-Za-z0-9._:-]{1,128}):winner=(?P<winner>[A-Za-z0-9._/-]{1,64}):bids=(?P<number>\d+)\Z"),
}
_GROUNDING_ACK_RE = re.compile(r"grounded:(?P<digest>[0-9a-f]{7,40})\b")

# AutoGen conversation-programming vocabulary — additive, no emitted bytes
# Wu et al. arXiv:2308.08155v2 §2.1 conversable agents / §2.2 conversation
# programming / Fig.2 unified interfaces & auto-reply / §3 A3-A6 patterns
# UNACTIVATED: no new runtime, no LLM call, no tool execution.
CONVERSATION_PATTERNS: Final[tuple[str, ...]] = ("static", "dynamic", "hierarchical", "group-chat")
"""Conversation patterns per AutoGen §2.2/Fig.3 — static (predefined order,
e.g. A1/A4) vs dynamic (content-driven via GroupChatManager, A5) vs
hierarchical (Commander/Writer/Safeguard, A4) vs group-chat (shared context
+ speaker selection, A5/A6)."""

AGENT_CAPABILITIES: Final[tuple[str, ...]] = ("llm", "human", "tool", "llm+tool", "human+tool", "llm+human+tool")
"""Agent capability mix per AutoGen §2.1 — LLM-backed / human-backed /
tool-backed and combinations; UserProxyAgent = human+tool, AssistantAgent =
llm."""

CHAT_TERMINATION_TOKENS: Final[tuple[str, ...]] = ("TERMINATE",)
"""Natural-language termination signal per AutoGen §2.2 — AssistantAgent
default system message instructs reply with TERMINATE when task completed;
mirrors Turn settled (...) at conversation scope."""

CONVERSABLE_ROLES: Final[tuple[str, ...]] = ("assistant", "user-proxy", "group-manager", "grounding", "safeguard", "commander")
"""Conversable role vocabulary per AutoGen Fig.2/§3 — AssistantAgent /
UserProxyAgent / GroupChatManager / grounding agent (A3) /
Safeguard+Writer+Commander (A4) / board agent (A6). Plant roles
(genome/health/witness/...) map to these, not replaced by them."""

AUTO_REPLY_LIMIT_DEFAULT: Final[int] = 10
"""Programming-language control counterpart per AutoGen §2.2 — max number of
auto replies before termination; same kind as human_input_mode NEVER/ALWAYS
but numeric."""

# Suchman 1994 guardrail on conversation-programming vocabulary — advisory, not required
# Suchman CSCW 2:177-190 "Do categories have politics?" §2-§4 (Springer
# https://link.springer.com/content/pdf/10.1007/BF00749015.pdf / LRI
# https://www.lri.fr/~mbl/ENS/CSCW/2017/papers/Suchman-JCSCW94.pdf),
# Bowers & Churcher 1988; Schegloff 1982/1988; Sacks 1979; Foucault 1979;
# Lynch 1990. Failure mode if categories taken as canonical/mandated:
# agenda of discipline/control, radical indeterminacy of illocutionary force,
# Lynch display of rationalistic commitments in Fig.5.1, field-observed
# selective ad-hoc appropriation (Johnson et al. 1986; Bullen & Bennett
# 1990). Disposition: CONVERSATION_*/AGENT_CAPABILITIES/TERMINATE vocabulary
# is advisory; decoders stay tolerant of uncategorized prose and must not
# coerce speakers to tag illocutionary force (cf. THE COORDINATOR enrolment).
# UNACTIVATED: no decoder gate change, no new runtime.
CHAT_VOCABULARY_POLICY: Final[str] = "advisory"
"""Suchman 1994 guardrail — the CONVERSATION_*/AGENT_CAPABILITIES/TERMINATE
vocabulary names optional annotations on the tape; decoders must remain tolerant
of uncategorized prose and must not coerce speakers to tag illocutionary force
(cf. THE COORDINATOR enrolment, Bowers & Churcher indeterminacy, field
selective appropriation)."""


def is_conversation_annotation_required(value: object) -> bool:
    """Whether a conversation annotation is required for decoding — always False.

    Suchman 1994 §2-§4: mandating universal speech-act categories coerces users
    facing radical indeterminacy of unfolding interaction; COORDINATOR field
    trials show ad-hoc selective ignoring. The tape stays advisory.
    """
    return False

# PARADISE metric vocabulary — Walker et al. 1997 P97-1035 §2.2-§2.4
# PARADISE: A Framework for Evaluating Spoken Dialogue Agents (ACL/EACL 1997,
# https://aclanthology.org/P97-1035.pdf) — Kappa success, ci costs over
# (sub)dialogues via AVM tagging, Z-score N(x), Performance=alpha N(K)-sum wi N(ci)
# with US regression; single confusion matrix inflates K when cross-attribute
# confusions few (p.274 fn.8). Added beside AutoGen/Suchman blocks as advisory
# vocabulary, same posture — no decoder gate, no emitted bytes until a future
# consumer (trial_metrics report or operator view) opts in. UNACTIVATED.
PARADISE_KAPPA_FORMULA: Final[str] = "K=(P(A)-P(E))/(1-P(E))"
"""Kappa per PARADISE §2.2 (Carletta 1996; Siegel & Castellan 1988) — chance-corrected agreement."""

PARADISE_COST_MEASURES: Final[tuple[str, ...]] = ("utterances", "repair_utterances", "elapsed_time", "inappropriate_utterances")
"""Example ci per PARADISE §2.3 — each ci(D) applicable to whole dialogue or subdialogue via AVM tagging; 1/N split for multi-attribute utterances."""

PARADISE_PERFORMANCE_SPEC: Final[str] = "Performance=alpha\u00b7N(K)-sum wi\u00b7N(ci)"
"""Performance per §2.4 — N(x)=(x-x_bar)/sigma, weights via regression on user satisfaction US; subdialogue-capable per §2.5."""


def paradise_kappa(*, agreements: int, total: int, chance: float) -> float | None:
    """K from P(A)=agreements/total and P(E)=chance; None when total==0 or chance==1.

    K=(P(A)-P(E))/(1-P(E)) per PARADISE §2.2. Pure, advisory; returns None on
    degenerate inputs (total 0, chance 1, type mismatch, agreements out of range).
    """
    if not isinstance(agreements, int) or isinstance(agreements, bool):
        return None
    if not isinstance(total, int) or isinstance(total, bool):
        return None
    if not isinstance(chance, (int, float)) or isinstance(chance, bool):
        return None
    if total == 0 or float(chance) == 1.0:
        return None
    if agreements < 0 or total < 0 or agreements > total:
        return None
    pe = float(chance)
    if pe < 0 or pe > 1:
        return None
    pa = agreements / total
    denom = 1 - pe
    if denom == 0:
        return None
    return (pa - pe) / denom


def paradise_normalize(value: float, mean: float, sd: float) -> float | None:
    """N(x)=(x-mean)/sd; None when sd==0.

    Z-score normalization per PARADISE §2.4 to put K and ci on comparable scale
    before weighting. Pure.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    if not isinstance(mean, (int, float)) or isinstance(mean, bool):
        return None
    if not isinstance(sd, (int, float)) or isinstance(sd, bool):
        return None
    if float(sd) == 0:
        return None
    return (float(value) - float(mean)) / float(sd)


def paradise_performance(*, kappa_norm: float, cost_norms: dict[str, float], weights: dict[str, float]) -> float | None:
    """alpha N(K) - sum wi N(ci); None on missing weight; pure, advisory.

    Per PARADISE §2.4 Performance = alpha·N(K) - sum wi·N(ci). weights dict
    carries ``alpha`` (or ``kappa`` alias) for K and wi keyed by cost name
    for each entry in cost_norms. Returns None if any required weight missing
    or types invalid. Pure.
    """
    if not isinstance(kappa_norm, (int, float)) or isinstance(kappa_norm, bool):
        return None
    if not isinstance(cost_norms, dict) or not isinstance(weights, dict):
        return None
    alpha = weights.get("alpha", weights.get("kappa"))
    if not isinstance(alpha, (int, float)) or isinstance(alpha, bool):
        return None
    result = float(alpha) * float(kappa_norm)
    for name, cn in cost_norms.items():
        if not isinstance(cn, (int, float)) or isinstance(cn, bool):
            return None
        wi = weights.get(name)
        if not isinstance(wi, (int, float)) or isinstance(wi, bool):
            return None
        result -= float(wi) * float(cn)
    return result


def paradise_cost_for_subdialogue(cost_full: float, attributes_in_utterance: int) -> float:
    """1/N split per §2.3 — utterance contributing to N attributes counts 1/N per attribute.

    Pure. Returns 0.0 on invalid inputs; when N<=0 returns cost_full unchanged
    (degenerate, no split).
    """
    if not isinstance(cost_full, (int, float)) or isinstance(cost_full, bool):
        return 0.0
    if not isinstance(attributes_in_utterance, int) or isinstance(attributes_in_utterance, bool):
        return float(cost_full)
    if attributes_in_utterance <= 0:
        return float(cost_full)
    return float(cost_full) / float(attributes_in_utterance)


def grounding_ack(entry: FeedEntry) -> str | None:
    """Digest referenced as positive evidence, or None.

    A line containing ``grounded:<digest>`` anywhere in the body counts as an
    acknowledgment continuer (Clark's form 1). The digest must be hex 7–40
    chars so a SHA prefix qualifies but free prose does not. The
    relevant-next-turn case (an outcome/wall edit whose content is the
    evidence) is not decoded here; it is observed by the next contribution's
    appropriateness.
    """
    m = _GROUNDING_ACK_RE.search(entry.body)
    return m.group("digest") if m else None


def conversation_pattern_is_valid(value: object) -> bool:
    """Whether *value* names a known conversation pattern (AutoGen §2.2)."""
    return isinstance(value, str) and value in CONVERSATION_PATTERNS


def agent_capability_is_valid(value: object) -> bool:
    """Whether *value* names a known agent capability mix (AutoGen §2.1)."""
    return isinstance(value, str) and value in AGENT_CAPABILITIES


def conversable_role_is_valid(value: object) -> bool:
    """Whether *value* names a known conversable role (AutoGen Fig.2/§3)."""
    return isinstance(value, str) and value in CONVERSABLE_ROLES


def is_termination_token(text: str) -> bool:
    """Whether *text* is the natural-language terminator (AutoGen §2.2)."""
    return isinstance(text, str) and text.strip() == "TERMINATE"


def auto_reply_limit_is_valid(value: object) -> bool:
    """Whether *value* is a valid auto-reply limit (AutoGen §2.2, 1..100)."""
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 100


def next_speaker_is_valid(value: object, participants: list[str]) -> bool:
    """GroupChatManager contract stub — speaker must be in participants.

    Documents the AutoGen §3 A5 invariant (shared-context speaker selection)
    without implementing selection logic. Pure predicate, no I/O.
    """
    return isinstance(value, str) and value in participants


class ProtocolError(FeedError):
    """A reserved control sentence cannot be decoded safely."""


@dataclass(frozen=True)
class LifecycleEvent:
    kind: str
    role: str
    observation: int | None = None
    event: int | None = None
    task: str | None = None
    wake: int | None = None
    continue_work: bool = False
    result: str | None = None
    prose: str = ""


@dataclass(frozen=True)
class SiteSyncCompletion:
    target: str
    core_sha: str


@dataclass(frozen=True)
class MarketEvent:
    kind: str
    identity: str
    actor: str = ""
    qual: str = ""
    number: int = 0


def decode_market(entry: FeedEntry) -> MarketEvent | None:
    """Decode reserved market records; malformed records remain visible errors."""
    lines = entry.body.splitlines()
    header = lines[0].strip() if lines else ""
    for kind, pattern in _MARKET_LEGACY.items():
        if header.startswith(kind + ":"):
            match = pattern.fullmatch(header)
            if not match:
                raise ProtocolError(f"entry {entry.sequence}: malformed market {kind}")
            values = match.groupdict()
            return MarketEvent(kind, values["identity"], values.get("winner") or entry.source,
                               values.get("qual") or "", int(values.get("number") or 0))
        marker = f"[task-{kind}] "
        if header.startswith(marker):
            try:
                identity = header[len(marker):]
                data = json.loads(lines[1])
                if not _MARKET_TOKEN.fullmatch(identity) or not isinstance(data, dict) or data.get("identity") != identity:
                    raise ValueError("invalid identity")
                actor = data.get("bidder", entry.source) if kind == "bid" else data.get("winner", "")
                qual = data.get("qual", "") if kind == "bid" else data.get("requires", "") if kind == "announce" else ""
                number = data.get("available_wake", 0) if kind == "bid" else data.get("bids", 0)
                if not isinstance(actor, str) or not isinstance(qual, str) or type(number) is not int or number < 0:
                    raise ValueError("invalid market fields")
                if (kind == "bid" and (not actor or not qual)) or (kind == "award" and not actor) or (kind == "announce" and not qual):
                    raise ValueError("missing market fields")
                return MarketEvent(kind, identity, actor, qual, number)
            except (ValueError, TypeError, IndexError) as exc:
                raise ProtocolError(f"entry {entry.sequence}: malformed market {kind}") from exc
        if header == f"[task-{kind}]":
            raise ProtocolError(f"entry {entry.sequence}: malformed market {kind}")
    return None


def has_task_opportunity(entry: FeedEntry, header: str) -> bool:
    """Check the canonical opportunity marker, leaving other prose opaque."""
    return header in entry.body.splitlines()


@lru_cache(maxsize=8192)
def _decode(body: str) -> LifecycleEvent | None:
    lines = body.splitlines()
    if not lines:
        return None
    header = lines[0]
    prose = "\n".join(lines[1:])
    if match := _WAKE_RE.fullmatch(header):
        return LifecycleEvent("wake", match[1], observation=int(match[2]),
                              event=int(match[3]) if match[3] else None,
                              task=match[4], prose=prose)
    if match := _YIELD_RE.fullmatch(header):
        result = next((found[1] for line in lines[1:] if (found := _SETTLED.match(line))), None)
        return LifecycleEvent("yield", match[1], wake=int(match[2]),
                              continue_work=bool(match[3]), result=result, prose=prose)
    if match := _CLEAR_RE.fullmatch(header):
        return LifecycleEvent("clear", match[1], wake=int(match[2]), prose=prose)
    if _RESERVED.match(header):
        raise ProtocolError("malformed lifecycle receipt")
    return None


def decode_lifecycle(entry: FeedEntry) -> LifecycleEvent | None:
    if entry.source != "seed":
        return None
    try:
        return _decode(entry.body)
    except ProtocolError as exc:
        raise ProtocolError(f"entry {entry.sequence}: {exc}") from exc


def decode_site_sync(entry: FeedEntry) -> SiteSyncCompletion | None:
    """Decode the coordinator's target-scoped successful-refresh marker."""
    if entry.source != "sync":
        return None
    header = entry.body.splitlines()[0] if entry.body else ""
    if not header.startswith("[site-sync]"):
        return None
    if not header.startswith(_SITE_SYNC_HEADER):
        raise ProtocolError(f"entry {entry.sequence}: malformed site-sync marker")
    try:
        data = json.loads(header[len(_SITE_SYNC_HEADER):])
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"entry {entry.sequence}: malformed site-sync payload") from exc
    if (not isinstance(data, dict) or set(data) != {"target", "core_sha"}
            or not isinstance(data["target"], str) or not data["target"]
            or len(data["target"]) > 255 or any(char in data["target"] for char in "\r\n\0")
            or not isinstance(data["core_sha"], str) or not _CORE_SHA_RE.fullmatch(data["core_sha"])):
        raise ProtocolError(f"entry {entry.sequence}: invalid site-sync payload")
    return SiteSyncCompletion(data["target"], data["core_sha"])


def decode_kernel_sync_target(identity: str) -> str | None:
    """Decode the target suffix in the coordinator's kernel-sync task identity."""
    match = _KERNEL_SYNC_TASK_RE.fullmatch(identity)
    return match[1] if match else None
