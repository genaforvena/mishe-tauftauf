from __future__ import annotations

import fcntl
import importlib.util
import json
import math
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .feed import Feed, FeedEntry
from .control_cache import identity as control_identity, read as read_control_cache, write as write_control_cache
from .external_view import DELTA_PUBLISH_QUESTION, DELTA_VERSION, FLEET_VERSION, projected_delta_publish_controls, projected_fleet_controls, projected_publish_controls, safe_fleet_view, safe_publish_delta_view, safe_publish_view
from .judges import Judgment, conservative_unknown, controls, document, run_external, run_external_batch
from .observations import compose_frame, discover, run_filter, run_projector, strip_owned_chrome, validate_slug
from .policy import load_policy
from .predictions import pending_predictions, replay_predictions

JUDGED_RE = re.compile(r"^judged ([a-z-]+) for top-pain ([a-z0-9-]+) on entry (\d+):", re.MULTILINE)
DISPOSITION_RE = re.compile(r"^entry (\d+) for top-pain ([a-z0-9-]+): (wake|observe|held|irrelevant|addressed)$", re.MULTILINE)
AUTOMATIC_WAKE_RE = re.compile(
    r"automatic channel=([a-z][a-z0-9-]{0,63}) event=[0-9]{20}-[a-f0-9]{32} "
    r"source=[a-z][a-z0-9-]{0,63} source-seq=[1-9][0-9]* "
    r"observed-at=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z "
    r"prompt-sha256=[a-f0-9]{64} status=(?:delivered|refused|unknown)\Z"
)
ASSESS_RE = re.compile(r"^prediction (\d+): (accepted|needs-reasoning)$", re.MULTILINE)
START_RE = re.compile(r"^mind starting top-pain ([a-z0-9-]+) for entry (\d+) attempt=(\d+)$", re.MULTILINE)
EXIT_RE = re.compile(r"^mind exited top-pain ([a-z0-9-]+) for entry (\d+) attempt=(\d+) code=(-?\d+)$", re.MULTILINE)
HANDOFF_RE = re.compile(r"^handoff top-pain ([a-z0-9-]+) invocation ([^\s]+)$", re.MULTILINE)
BOOKKEEPING_PREFIXES = (
    "judged ", "entry ", "mind starting ", "mind exited ", "mind stdout ", "mind stderr ",
    "mind blocked ", "mind output ", "mind model ", "top-pain ", "prediction ",
    "desired state for prediction ", "handoff top-pain ",
    "wake requested ", "wake delivered ", "wake refused ", "UNKNOWN automatic-wake ",
)


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp() -> str:
    return now().isoformat(timespec="microseconds").replace("+00:00", "Z")


def runtime_status(entries: list[FeedEntry], slug: str) -> str:
    for entry in reversed(entries):
        if entry.source == "mishe-tauftauf" and (f"top-pain {slug}" in entry.body or f"top-pain {slug} " in entry.body):
            return entry.body.splitlines()[0][:180]
    return "quiet"


def is_bookkeeping(entry: FeedEntry) -> bool:
    return entry.source == "mishe-tauftauf" and entry.body.startswith(BOOKKEEPING_PREFIXES)


def judgment_receipt(feed: Feed, judgment: Judgment, slug: str, sequence: int, *, policy_version: str = "1", question_version: str = "1") -> FeedEntry:
    probability = "unknown" if judgment.probability is None else f"{judgment.probability:.17g}"
    body = (
        f"judged {judgment.question} for top-pain {slug} on entry {sequence}: {judgment.outcome} "
        f"probability={probability} question-version={question_version} policy-version={policy_version}"
    )
    return feed.append_runtime("mishe-tauftauf", body)


@dataclass
class RuntimeConfig:
    home: Path
    judge: Path | None = None
    launcher: str = "tmux"
    session: str = "mishe-tauftauf"
    interval: float = 5.0
    dispatch: bool = True
    policy: Path | None = None
    batch_judge: Path | None = None
    control_cache_ttl: float | None = None
    refresh_controls: bool = False
    external_view_slugs: tuple[str, ...] = ()
    external_delta_view_slugs: tuple[str, ...] = ()
    slug: str | None = None
    laya_structured: bool = False
    external_fleet_view_slugs: tuple[str, ...] = ()


class Coordinator:
    def __init__(self, config: RuntimeConfig):
        if config.judge is not None and config.batch_judge is not None:
            raise ValueError("select either a one-line judge or a batch judge")
        if config.laya_structured and (not config.external_delta_view_slugs or config.judge is not None or config.batch_judge is not None):
            raise ValueError("structured Laya requires projected delta view and no external judge")
        if len(set(config.external_view_slugs)) != len(config.external_view_slugs):
            raise ValueError("external view slugs must be unique")
        for slug in config.external_view_slugs:
            validate_slug(slug)
        for slug in config.external_delta_view_slugs:
            validate_slug(slug)
        if len(set(config.external_delta_view_slugs)) != len(config.external_delta_view_slugs) or set(config.external_view_slugs) & set(config.external_delta_view_slugs):
            raise ValueError("external view modes must be unique per slug")
        if len(set(config.external_fleet_view_slugs)) != len(config.external_fleet_view_slugs):
            raise ValueError("external fleet view slugs must be unique")
        if set(config.external_fleet_view_slugs) & (set(config.external_view_slugs) | set(config.external_delta_view_slugs)):
            raise ValueError("external view modes must be unique per slug")
        for slug in config.external_fleet_view_slugs:
            validate_slug(slug)
        if config.refresh_controls and config.control_cache_ttl is None:
            raise ValueError("--refresh-controls requires --control-cache-ttl")
        if config.control_cache_ttl is not None:
            ttl = config.control_cache_ttl
            if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not math.isfinite(ttl) or not 0 < ttl <= 86400:
                raise ValueError("control cache TTL must be in (0, 86400] seconds")
            if config.judge is None and config.batch_judge is None:
                raise ValueError("control cache requires an explicit executable judge")
        self.config = config
        self.home = config.home
        self._selected_slugs()
        self.feed = Feed(self.home)
        self.policy = load_policy(config.policy)
        self.previous: dict[str, str] = {}
        self.filter_identity: dict[str, tuple[int, int] | None] = {}
        self.known_slugs = self._replay_known_slugs()
        self._lock_handle = None
        self._launched_slugs: set[str] = set()
        if config.control_cache_ttl is None:
            self.control_failures = self._run_controls()
        else:
            adapter = config.judge or config.batch_judge
            cache_path = self.home / "control-cache.json"
            try:
                fingerprint = control_identity(adapter, "batch" if config.batch_judge else "single", self.policy, projected=bool(config.external_view_slugs), paired=bool(config.external_delta_view_slugs), fleet=bool(config.external_fleet_view_slugs))
            except OSError:
                fingerprint = None
            if fingerprint is None:
                names = self._control_names()
                self.control_failures = {name: "startup controls UNKNOWN: judge executable unavailable" for name in names}
            elif config.refresh_controls:
                self.control_failures = self._run_controls()
                try:
                    unchanged = control_identity(adapter, "batch" if config.batch_judge else "single", self.policy, projected=bool(config.external_view_slugs), paired=bool(config.external_delta_view_slugs), fleet=bool(config.external_fleet_view_slugs)) == fingerprint
                except OSError:
                    unchanged = False
                if unchanged:
                    try:
                        write_control_cache(cache_path, fingerprint, self.control_failures, projected=bool(config.external_view_slugs), paired=bool(config.external_delta_view_slugs), fleet=bool(config.external_fleet_view_slugs))
                    except OSError:
                        names = self._control_names()
                        self.control_failures = {name: "startup controls UNKNOWN: cache write failed" for name in names}
                else:
                    names = self._control_names()
                    self.control_failures = {name: "startup controls UNKNOWN: judge executable changed during refresh" for name in names}
            else:
                self.control_failures = read_control_cache(cache_path, fingerprint, config.control_cache_ttl, projected=bool(config.external_view_slugs), paired=bool(config.external_delta_view_slugs), fleet=bool(config.external_fleet_view_slugs))

    def _control_names(self) -> set[str]:
        return set(controls()) | ({"projected-publish"} if self.config.external_view_slugs else set()) | ({"projected-delta-publish"} if self.config.external_delta_view_slugs else set()) | ({"fleet-publish", "fleet-desired-state-met"} if self.config.external_fleet_view_slugs else set())

    def _raw_judge(self, question: str, slug: str, pane: str, evidence: str, prediction: str | None = None, *, question_text: str | None = None, structured: bool = False) -> Judgment:
        question_text = question_text or self.policy.question_text(question)
        if self.config.batch_judge is not None:
            return run_external_batch(self.config.batch_judge, (question,), slug, pane, evidence, prediction, timeout=self.policy.judge_timeout, question_texts={question: question_text})[question]
        if self.config.judge is not None:
            return run_external(self.config.judge, question, slug, pane, evidence, prediction, timeout=self.policy.judge_timeout, question_text=question_text)
        if importlib.util.find_spec("laya") is None:
            return conservative_unknown(question, slug, pane, evidence, prediction, question_text=question_text)
        from .laya_judge import judge as laya_judge, judge_structured
        request = document(question, slug, pane, evidence, prediction, question_text=question_text)
        if structured:
            try:
                state = json.loads(evidence)
                if (not isinstance(state, dict) or set(state) != {"version", "previous", "current"}
                    or safe_publish_delta_view(state["previous"], state["current"]) != evidence):
                    raise ValueError("invalid projected pair")
            except (ValueError, KeyError, TypeError):
                return conservative_unknown(question, slug, "[withheld]", "[withheld]", reason="invalid structured projected pair", question_text=question_text)
            probability, reason = judge_structured(state, question_text)
        else:
            probability, reason = laya_judge(request)
        return Judgment(
            question,
            probability,
            self.policy.classify(question, probability),
            reason or "Laya typed-decisions judgment",
            request,
        )

    def _run_controls(self) -> dict[str, str]:
        failures: dict[str, str] = {}
        for question, (positive, negative) in controls().items():
            yes = self._raw_judge(question, "control", "CONTROL TOP PAIN", positive)
            no = self._raw_judge(question, "control", "CONTROL TOP PAIN", negative)
            if self.policy.classify(question, yes.probability) != "yes" or self.policy.classify(question, no.probability) != "no":
                failures[question] = (
                    f"production controls failed: positive={yes.outcome}"
                    f"/{yes.probability}, negative={no.outcome}/{no.probability}"
                )
        if self.config.external_view_slugs:
            positive, negative = projected_publish_controls()
            yes = self._raw_judge("publish", "control", positive, positive)
            no = self._raw_judge("publish", "control", negative, negative)
            if self.policy.classify("publish", yes.probability) != "yes" or self.policy.classify("publish", no.probability) != "no":
                failures["projected-publish"] = (
                    f"projected controls failed: positive={yes.outcome}/{yes.probability}, "
                    f"negative={no.outcome}/{no.probability}"
                )
        if self.config.external_delta_view_slugs:
            positive, negative = projected_delta_publish_controls()
            yes = self._raw_judge("publish", "control", positive, positive, question_text=DELTA_PUBLISH_QUESTION, structured=self.config.laya_structured)
            no = self._raw_judge("publish", "control", negative, negative, question_text=DELTA_PUBLISH_QUESTION, structured=self.config.laya_structured)
            if self.policy.classify("publish", yes.probability) != "yes" or self.policy.classify("publish", no.probability) != "no":
                failures["projected-delta-publish"] = (
                    f"projected delta controls failed: positive={yes.outcome}/{yes.probability}, "
                    f"negative={no.outcome}/{no.probability}"
                )
        if self.config.external_fleet_view_slugs:
            red, green = projected_fleet_controls()
            for question, positive, negative in (("publish", red, green),
                                                 ("desired-state-met", green, red)):
                yes = self._raw_judge(question, "control", positive, positive)
                no = self._raw_judge(question, "control", negative, negative)
                if (self.policy.classify(question, yes.probability) != "yes"
                    or self.policy.classify(question, no.probability) != "no"):
                    failures["fleet-" + question] = (
                        f"fleet controls failed: positive={yes.outcome}/{yes.probability}, "
                        f"negative={no.outcome}/{no.probability}")
        return failures

    def _replay_known_slugs(self) -> set[str]:
        state: set[str] = set()
        for entry in self.feed.entries():
            if entry.source != "mishe-tauftauf":
                continue
            match = re.fullmatch(r"top-pain ([a-z0-9-]+) (live|absent)", entry.body)
            if not match:
                continue
            if match.group(2) == "live":
                state.add(match.group(1))
            else:
                state.discard(match.group(1))
        return state

    def _selected_slugs(self) -> list[str]:
        slugs = discover(self.home)
        if self.config.slug is None:
            return slugs
        slug = validate_slug(self.config.slug)
        if slug not in slugs:
            raise ValueError(f"top-pain {slug} is missing or not executable")
        return [slug]

    def acquire(self) -> None:
        path = self.home / ".runner.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_handle = path.open("a+")
        try:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock_handle.close()
            self._lock_handle = None
            raise RuntimeError(f"another coordinator holds {path}") from exc

    def close(self) -> None:
        if self._lock_handle:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            self._lock_handle.close()
            self._lock_handle = None

    def _judge(self, question: str, slug: str, pane: str, evidence: str, prediction: str | None = None, *, previous_projection: str | None = None, event_source: str | None = None) -> Judgment:
        paired = slug in self.config.external_delta_view_slugs
        question_text = DELTA_PUBLISH_QUESTION if paired and question == "publish" else self.policy.question_text(question)
        if slug in self.config.external_fleet_view_slugs:
            # A routed entry must be an observation owned by this exact slug.
            # Due predictions, feed prose, pane content and prediction notes have
            # no independently authenticated fleet projection.
            if question != "publish" and (event_source != f"observation/{slug}" or question not in
                                          ("relevance", "desired-state-met", "continue-observing")):
                return conservative_unknown(question, slug, "[withheld]", "[withheld]",
                                            reason="fleet observation unavailable for this question", question_text=question_text)
            safe = safe_fleet_view(evidence, slug)
            if safe is None:
                return conservative_unknown(question, slug, "[withheld]", "[withheld]",
                                            reason="invalid fleet projection", question_text=question_text)
            state = safe.split("\n", 1)[0].removeprefix("STATE: ")
            fresh = "source=top-pane/" in safe and " freshness=fresh " in safe
            value_fresh = any(f"{name}=fresh" in safe.split() for name in ("goal", "comments", "cleaner", "journal"))
            if question == "relevance":
                request = document(question, slug, safe, safe, question_text=question_text)
                return Judgment(question, 1.0, "yes", "validated exact-owner observation", request)
            if question in ("desired-state-met", "continue-observing"):
                if state in ("UNKNOWN", "INTERMEDIATE") or "source=top-pane/" not in safe:
                    return conservative_unknown(question, slug, safe, safe,
                                                reason="fleet state does not establish an owner verdict", question_text=question_text)
                if state == "GREEN" and not (fresh and value_fresh):
                    return conservative_unknown(question, slug, safe, safe,
                                                reason="fresh desired-state evidence not established", question_text=question_text)
            fleet_failure = self.control_failures.get("fleet-" + question)
            if fleet_failure:
                return conservative_unknown(question, slug, safe, safe,
                                            reason=fleet_failure, question_text=question_text)
            pane, evidence, prediction = safe, safe, None
        elif slug in self.config.external_view_slugs or paired:
            if question != "publish":
                return conservative_unknown(question, slug, "[withheld]", "[withheld]", reason="external view unavailable for this question", question_text=question_text)
            safe = safe_publish_delta_view(previous_projection, evidence) if paired else safe_publish_view(evidence)
            if safe is None:
                return conservative_unknown(question, slug, "[withheld]", "[withheld]", reason="external view invalid or previous view absent", question_text=question_text)
            if paired and safe_publish_view(previous_projection) == safe_publish_view(evidence):
                request = document(question, slug, safe, safe, question_text=question_text)
                return Judgment(question, 0.0, "no", "identical validated projected views", request)
            pane, evidence, prediction = safe, safe, None
            projected_failure = self.control_failures.get("projected-delta-publish" if paired else "projected-publish")
            if projected_failure:
                return conservative_unknown(question, slug, pane, evidence, reason=projected_failure, question_text=question_text)
        failure = None if paired and self.config.laya_structured and question == "publish" else self.control_failures.get(question)
        if failure:
            return conservative_unknown(question, slug, pane, evidence, prediction, failure, question_text=question_text)
        try:
            judgment = self._raw_judge(question, slug, pane, evidence, prediction, question_text=question_text,
                                       structured=paired and self.config.laya_structured and question == "publish")
        except Exception:
            return conservative_unknown(question, slug, pane, evidence, prediction, "judge failed", question_text=question_text)
        return Judgment(judgment.question, judgment.probability, self.policy.classify(question, judgment.probability), judgment.reason, judgment.document)

    def _judge_batch(self, questions: tuple[str, ...], slug: str, pane: str, evidence: str, prediction: str | None = None) -> dict[str, Judgment]:
        if slug in self.config.external_view_slugs or slug in self.config.external_delta_view_slugs or slug in self.config.external_fleet_view_slugs:
            return {q: self._judge(q, slug, pane, evidence, prediction) for q in questions}
        if self.config.batch_judge is None:
            return {q: self._judge(q, slug, pane, evidence, prediction) for q in questions}
        active = tuple(q for q in questions if q not in self.control_failures)
        try:
            raw = run_external_batch(self.config.batch_judge, active, slug, pane, evidence, prediction, timeout=self.policy.judge_timeout, question_texts={q: self.policy.question_text(q) for q in active}) if active else {}
        except Exception:
            raw = {q: conservative_unknown(q, slug, pane, evidence, prediction, "batch judge failed", question_text=self.policy.question_text(q)) for q in active}
        return {
            q: Judgment(raw[q].question, raw[q].probability, self.policy.classify(q, raw[q].probability), raw[q].reason, raw[q].document)
            if q in raw else conservative_unknown(q, slug, pane, evidence, prediction, self.control_failures[q], question_text=self.policy.question_text(q))
            for q in questions
        }

    def _receipt(self, judgment: Judgment, slug: str, sequence: int) -> FeedEntry:
        version = (f"{DELTA_VERSION}{'.structured-laya-v1' if self.config.laya_structured else ''}.{self.policy.question_version(judgment.question)}"
                   if judgment.question == "publish" and slug in self.config.external_delta_view_slugs
                   else f"{FLEET_VERSION}.{self.policy.question_version(judgment.question)}"
                   if slug in self.config.external_fleet_view_slugs
                   else self.policy.question_version(judgment.question))
        return judgment_receipt(self.feed, judgment, slug, sequence, policy_version=self.policy.version, question_version=version)

    def _pane(self, slug: str) -> str:
        if self.config.launcher == "tmux":
            from .tmux import capture_top
            return capture_top(self.home, self.config.session, slug)
        return compose_frame(self.home, slug).body

    def _filter_identity(self, slug: str) -> tuple[int, int] | None:
        path = self.home / "filters" / slug
        try:
            stat = path.stat()
            return stat.st_mtime_ns, stat.st_size
        except FileNotFoundError:
            return None

    def observe(self) -> list[FeedEntry]:
        current_slugs = set(self._selected_slugs())
        known_slugs = self.known_slugs if self.config.slug is None else self.known_slugs & current_slugs
        emitted: list[FeedEntry] = []
        entries = self.feed.entries()
        for slug in sorted(current_slugs - known_slugs):
            emitted.append(self.feed.append_runtime("mishe-tauftauf", f"top-pain {slug} live"))
        for slug in sorted(known_slugs - current_slugs):
            emitted.append(self.feed.append_runtime("mishe-tauftauf", f"top-pain {slug} absent"))
        self.known_slugs = current_slugs if self.config.slug is None else self.known_slugs | current_slugs
        for slug in sorted(current_slugs):
            pane = self._pane(slug)
            raw = strip_owned_chrome(pane, strip_expectations=True)
            identity = self._filter_identity(slug)
            if self.filter_identity.get(slug) != identity:
                previous = ""
                self.filter_identity[slug] = identity
            else:
                previous = self.previous.get(slug, "")
            result = run_filter(self.home, slug, previous, raw)
            self.previous[slug] = raw
            if result.diagnostic:
                emitted.append(self.feed.append_runtime("observation/observability", result.diagnostic))
            if not result.passed:
                continue
            evidence = run_projector(self.home, slug, previous, raw)
            fleet = slug in self.config.external_fleet_view_slugs
            canonical = safe_fleet_view(evidence, slug) if fleet else evidence
            if canonical is None:
                continue
            latest = next((entry.body for entry in reversed(self.feed.entries()) if entry.source == f"observation/{slug}"), None)
            previous_safe = safe_fleet_view(latest, slug) if fleet and latest is not None else latest
            if canonical == previous_safe:
                continue
            judgment = self._judge("publish", slug, pane, evidence, previous_projection=latest)
            self._receipt(judgment, slug, 0)
            if judgment.outcome != "no":
                safe = (safe_fleet_view(evidence, slug) if slug in self.config.external_fleet_view_slugs
                        else safe_publish_view(evidence) if slug in (*self.config.external_view_slugs, *self.config.external_delta_view_slugs)
                        else evidence)
                if safe is not None:
                    emitted.append(self.feed.append_runtime(f"observation/{slug}", safe if fleet else evidence))
        return emitted

    def assess_predictions(self) -> None:
        entries = self.feed.entries()
        predictions = replay_predictions(self.home, entries)
        assessed = {int(seq) for entry in entries if entry.source == "mishe-tauftauf" for seq, _ in ASSESS_RE.findall(entry.body)}
        for prediction in sorted(predictions.values(), key=lambda item: item.sequence):
            if self.config.slug is not None and prediction.slug != self.config.slug:
                continue
            if prediction.sequence in assessed:
                continue
            pane = self._pane(prediction.slug)
            linked = prediction.plan.read_text(encoding="utf-8") if prediction.plan else "(none)"
            evidence = f"LIVE PANE\n{pane}\nLINKED PLAN\n{linked}\nPREDICTION NOTE\n{prediction.body}"
            judgment = self._judge("valid-attempt", prediction.slug, pane, evidence, prediction.body)
            self._receipt(judgment, prediction.slug, prediction.sequence)
            state = "accepted" if judgment.outcome == "yes" else "needs-reasoning"
            self.feed.append_runtime("mishe-tauftauf", f"prediction {prediction.sequence}: {state}")

    def due_predictions(self) -> None:
        entries = self.feed.entries()
        for prediction in pending_predictions(self.home, entries):
            if self.config.slug is not None and prediction.slug != self.config.slug:
                continue
            if prediction.check_at > now():
                continue
            pane = self._pane(prediction.slug)
            evidence = f"FRESH AT {stamp()}\n{pane}"
            decisions = self._judge_batch(("prediction-met", "desired-state-met"), prediction.slug, pane, evidence, prediction.body)
            met = decisions["prediction-met"]
            desired = decisions["desired-state-met"]
            self._receipt(met, prediction.slug, prediction.sequence)
            self._receipt(desired, prediction.slug, prediction.sequence)
            outcome = "met" if met.outcome == "yes" else ("missed" if met.outcome == "no" else "insufficient-evidence")
            desired_outcome = "observed" if desired.outcome == "yes" else "not-established"
            self.feed.append_runtime("mishe-tauftauf", f"prediction {prediction.sequence}: {outcome}\nevidence acquired: {stamp()}")
            self.feed.append_runtime("mishe-tauftauf", f"desired state for prediction {prediction.sequence}: {desired_outcome}")
            if outcome != "met" or desired_outcome != "observed":
                self.feed.append_runtime("observation/" + prediction.slug, f"prediction {prediction.sequence} requires reasoning: {outcome}; desired state {desired_outcome}")

    def route(self) -> None:
        entries = self.feed.entries()
        dispositions = {}
        latest_observation = {}
        for recorded in entries:
            if recorded.source.startswith("observation/"):
                latest_observation[recorded.source.removeprefix("observation/")] = recorded.sequence
            if recorded.source == "mishe-tauftauf":
                for seq, slug, outcome in DISPOSITION_RE.findall(recorded.body):
                    dispositions[(int(seq), slug)] = (outcome, recorded.timestamp)
        slugs = self._selected_slugs()
        for entry in entries:
            if is_bookkeeping(entry) or entry.source.startswith(("prediction/", "mind/")):
                continue
            targets = slugs
            if entry.source.startswith("observation/") and entry.source != "observation/observability":
                target = entry.source.removeprefix("observation/")
                targets = [target] if target in slugs else []
            elif entry.source == "automatic-wake":
                match = AUTOMATIC_WAKE_RE.fullmatch(entry.body)
                if match is None or match.group(1) not in slugs:
                    self.feed.append_runtime_once(
                        "mishe-tauftauf", f"UNKNOWN automatic-wake entry {entry.sequence}: target unavailable")
                    continue
                targets = [match.group(1)]
            for slug in targets:
                fleet = slug in self.config.external_fleet_view_slugs
                prior = dispositions.get((entry.sequence, slug))
                if prior is not None:
                    outcome, recorded_at = prior
                    if outcome != "held" or not fleet or latest_observation.get(slug) != entry.sequence:
                        continue
                    try:
                        age = (now() - datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))).total_seconds()
                    except (TypeError, ValueError):
                        continue
                    if age < 600:
                        continue
                if fleet and entry.source != f"observation/{slug}":
                    relevance = self._judge("relevance", slug, "[withheld]", entry.body, event_source=entry.source)
                    self._receipt(relevance, slug, entry.sequence)
                    # This feed event cannot testify about this fleet channel.
                    self.feed.append_runtime("mishe-tauftauf", f"entry {entry.sequence} for top-pain {slug}: observe")
                    continue
                pane = self._pane(slug)
                source = {"event_source": entry.source} if fleet else {}
                relevance = self._judge("relevance", slug, pane, entry.body, **source)
                self._receipt(relevance, slug, entry.sequence)
                if relevance.outcome == "no":
                    self.feed.append_runtime("mishe-tauftauf", f"entry {entry.sequence} for top-pain {slug}: irrelevant")
                    continue
                pending = pending_predictions(self.home, self.feed.entries(), slug)
                if pending:
                    prediction_text = "\n\n".join(item.body for item in pending)
                    gate = self._judge("continue-observing", slug, pane, entry.body, prediction_text, **source)
                    self._receipt(gate, slug, entry.sequence)
                else:
                    gate = self._judge("desired-state-met", slug, pane, entry.body, **source)
                    self._receipt(gate, slug, entry.sequence)
                disposition = ("observe" if gate.outcome == "yes" else
                               "held" if fleet and gate.outcome != "no" else "wake")
                self.feed.append_runtime("mishe-tauftauf", f"entry {entry.sequence} for top-pain {slug}: {disposition}")
                if disposition == "wake":
                    self._request_wake(slug, entry)

    def _request_wake(self, slug: str, stimulus: FeedEntry) -> None:
        marker = f"wake requested top-pain {slug} for entry {stimulus.sequence}"
        self.feed.append_runtime_once("mishe-tauftauf", marker)
        if self.config.dispatch:
            self.invoke(slug, stimulus)

    def _attempt(self, slug: str, sequence: int) -> int:
        entries = self.feed.entries()
        return 1 + sum(1 for entry in entries if entry.source == "mishe-tauftauf" for s, seq, _ in START_RE.findall(entry.body) if s == slug and int(seq) == sequence)

    def _context(self, slug: str, stimulus: FeedEntry, invocation: str) -> str:
        pane_command = f"mishe-tauftauf --home {self.home} pain read {slug}"
        handoff = self.home / "handoffs" / f"{slug}.md"
        handoff_text = handoff.read_text(encoding="utf-8") if handoff.exists() else "(none)"
        handoff_text = self._bounded_text(handoff_text, 8 * 1024)
        trigger = self._bounded_text(stimulus.body, 8 * 1024)
        predictions = pending_predictions(self.home, self.feed.entries(), slug)
        prediction_text = self._bounded_text("\n\n".join(p.body for p in predictions) or "(none)", 4 * 1024)
        history = self._routed_history()
        instructions = (Path(__file__).resolve().parents[2] / "instructions" / "mind.txt").read_text(encoding="utf-8")
        return (
            f"TRIGGERING EVENT {stimulus.sequence}\n{trigger}\n\n"
            "Read your current live Top Pain before deciding or acting.\n"
            f"Exact read command: {pane_command}\n"
            f"Invocation: {invocation}\nHandoff path: {handoff}\nCURRENT HANDOFF\n{handoff_text}\n\n"
            f"ACTIVE PREDICTIONS\n{prediction_text}\n\n"
            f"ROUTED HISTORY\n{history}\n\nINSTRUCTIONS\n{instructions}\n"
        )

    @staticmethod
    def _bounded_text(value: str, limit: int) -> str:
        raw = value.encode("utf-8")
        if len(raw) <= limit:
            return value
        return raw[:limit - 32].decode("utf-8", "ignore") + "\n[context truncated]"

    def _routed_history(self) -> str:
        """Select complete recent receipts within the context budget."""
        limit = 24 * 1024
        selected: list[str] = []
        used = 0
        for entry in reversed(self.feed.entries()):
            if entry.source.startswith("observation/") or entry.source == "mishe-tauftauf":
                text = f"[{entry.sequence} {entry.source}] {entry.body}\n"
                size = len(text.encode("utf-8"))
                if size <= limit - used:
                    selected.append(text)
                    used += size
        return "".join(reversed(selected))

    def invoke(self, slug: str, stimulus: FeedEntry) -> None:
        # One coordinator pass can replay several wake entries for the same
        # channel. Only one tmux respawn may be issued before its pane has
        # acquired the per-Mind lease.
        if self.config.launcher == "tmux" and slug in self._launched_slugs:
            return
        executable = self.home / "minds" / slug
        if not (executable.is_file() and os.access(executable, os.X_OK)):
            executable = self.home / "minds" / "default"
        if not (executable.is_file() and os.access(executable, os.X_OK)):
            marker = f"mind blocked top-pain {slug}: no executable minds/{slug} or minds/default"
            if not any(entry.source == "mishe-tauftauf" and entry.body == marker for entry in self.feed.entries()):
                self.feed.append_runtime("mishe-tauftauf", marker)
            return
        lock_path = self.home / "minds" / f".{slug}.lock"
        lock = lock_path.open("a+")
        try:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            if self.config.launcher == "tmux":
                self._launched_slugs.add(slug)
            attempt = self._attempt(slug, stimulus.sequence)
            invocation = f"{slug}-{stimulus.sequence}-{attempt}-{int(time.time())}"
            self.feed.append_runtime("mishe-tauftauf", f"mind starting top-pain {slug} for entry {stimulus.sequence} attempt={attempt}")
            context = self._context(slug, stimulus, invocation)
            if self.config.launcher == "tmux":
                from .tmux import launch_mind
                launch_mind(self.home, self.config.session, slug, stimulus.sequence, attempt, invocation, context)
                return
            env = os.environ.copy()
            env.update({
                "MISHE_TAUFTAUF_HOME": str(self.home), "MISHE_TAUFTAUF_SLUG": slug,
                "MISHE_TAUFTAUF_INVOCATION": invocation, "MISHE_TAUFTAUF_WORKSPACE": str(self.home.parent),
            })
            try:
                # Retain the lease and leave the coordinator pane's process group;
                # closing that pane must not SIGHUP an in-flight headless Mind.
                result = subprocess.run([str(executable)], input=context.encode("utf-8"),
                                        capture_output=True, env=env, pass_fds=(lock.fileno(),),
                                        start_new_session=True)
                code, stdout, stderr = result.returncode, result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")
            except OSError as exc:
                code, stdout, stderr = 127, "", str(exc)
            if stdout or stderr:
                self.feed.append_runtime("mishe-tauftauf", f"mind output top-pain {slug} invocation {invocation} stdout-bytes={len(stdout.encode())} stderr-bytes={len(stderr.encode())}")
            self.feed.append_runtime("mishe-tauftauf", f"mind exited top-pain {slug} for entry {stimulus.sequence} attempt={attempt} code={code}")
            if not any(entry.source == "mishe-tauftauf" and f"handoff top-pain {slug} invocation {invocation}" in entry.body for entry in self.feed.entries()):
                self.feed.append_runtime("observation/" + slug, f"UNKNOWN — mind invocation {invocation} exited without a tied handoff; prior handoff is stale")
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    def retry_unfinished_wakes(self) -> None:
        if not self.config.dispatch:
            return
        entries = self.feed.entries()
        by_sequence = {entry.sequence: entry for entry in entries}
        dispositions: dict[tuple[int, str], str] = {}
        starts: dict[tuple[int, str], set[int]] = {}
        exits: dict[tuple[int, str], set[int]] = {}
        for entry in entries:
            if entry.source != "mishe-tauftauf":
                continue
            for sequence, slug, disposition in DISPOSITION_RE.findall(entry.body):
                dispositions[(int(sequence), slug)] = disposition
            for slug, sequence, attempt in START_RE.findall(entry.body):
                starts.setdefault((int(sequence), slug), set()).add(int(attempt))
            for slug, sequence, attempt, _code in EXIT_RE.findall(entry.body):
                exits.setdefault((int(sequence), slug), set()).add(int(attempt))
        for key in sorted(key for key, disposition in dispositions.items() if disposition == "wake"):
            sequence, slug = key
            if self.config.slug is not None and slug != self.config.slug:
                continue
            attempted = starts.get(key, set())
            finished = exits.get(key, set())
            if attempted and max(attempted) in finished:
                continue
            stimulus = by_sequence.get(sequence)
            if stimulus is None:
                continue
            fleet = slug in self.config.external_fleet_view_slugs
            if fleet and stimulus.source != f"observation/{slug}":
                continue
            if attempted:
                # A tmux Mind holds this lease for its whole run; a headless
                # Mind inherits it. Never respawn over a live invocation.
                with (self.home / "minds" / f".{slug}.lock").open("a+") as lease:
                    try:
                        fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    else:
                        fcntl.flock(lease.fileno(), fcntl.LOCK_UN)
                pane = self._pane(slug)
                source = {"event_source": stimulus.source} if fleet else {}
                reassessment = self._judge("desired-state-met", slug, pane, stimulus.body, **source)
                if reassessment.outcome == "yes":
                    self._receipt(reassessment, slug, sequence)
                    self.feed.append_runtime("mishe-tauftauf", f"entry {sequence} for top-pain {slug}: addressed")
                else:
                    marker = f"UNKNOWN — unfinished Mind for entry {sequence} top-pain {slug}; effect requires reconciliation before retry"
                    if not any(entry.source == f"observation/{slug}" and entry.body == marker
                               for entry in self.feed.entries()):
                        self._receipt(reassessment, slug, sequence)
                        self.feed.append_runtime(f"observation/{slug}", marker)
                continue
            if not attempted:
                pane = self._pane(slug)
                source = {"event_source": stimulus.source} if fleet else {}
                active_predictions = pending_predictions(self.home, self.feed.entries(), slug)
                if active_predictions:
                    prediction_text = "\n\n".join(item.body for item in active_predictions)
                    reassessment = self._judge(
                        "continue-observing", slug, pane, stimulus.body, prediction_text, **source,
                    )
                else:
                    reassessment = self._judge("desired-state-met", slug, pane, stimulus.body, **source)
                self._receipt(reassessment, slug, sequence)
                if reassessment.outcome == "yes":
                    self.feed.append_runtime("mishe-tauftauf", f"entry {sequence} for top-pain {slug}: addressed")
                    continue
            self.invoke(slug, stimulus)


    def pass_once(self) -> None:
        self.retry_unfinished_wakes()
        self.observe()
        self.assess_predictions()
        self.due_predictions()
        self.route()

    def follow(self) -> None:
        while True:
            self.pass_once()
            pending = pending_predictions(self.home, self.feed.entries(), self.config.slug)
            delay = self.config.interval
            if pending:
                delay = min(delay, max(0.0, (pending[0].check_at - now()).total_seconds()))
            time.sleep(max(0.05, delay))
