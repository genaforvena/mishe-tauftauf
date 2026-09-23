from __future__ import annotations

import fcntl
import importlib.util
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .feed import Feed, FeedEntry
from .judges import Judgment, conservative_unknown, controls, document, run_external, run_external_batch
from .observations import compose_frame, discover, run_filter, run_projector, strip_owned_chrome
from .policy import load_policy
from .predictions import pending_predictions, replay_predictions

JUDGED_RE = re.compile(r"^judged ([a-z-]+) for top-pain ([a-z0-9-]+) on entry (\d+):", re.MULTILINE)
DISPOSITION_RE = re.compile(r"^entry (\d+) for top-pain ([a-z0-9-]+): (wake|observe|irrelevant|addressed)$", re.MULTILINE)
ASSESS_RE = re.compile(r"^prediction (\d+): (accepted|needs-reasoning)$", re.MULTILINE)
START_RE = re.compile(r"^mind starting top-pain ([a-z0-9-]+) for entry (\d+) attempt=(\d+)$", re.MULTILINE)
EXIT_RE = re.compile(r"^mind exited top-pain ([a-z0-9-]+) for entry (\d+) attempt=(\d+) code=(-?\d+)$", re.MULTILINE)
HANDOFF_RE = re.compile(r"^handoff top-pain ([a-z0-9-]+) invocation ([^\s]+)$", re.MULTILINE)
BOOKKEEPING_PREFIXES = (
    "judged ", "entry ", "mind starting ", "mind exited ", "mind stdout ", "mind stderr ",
    "mind blocked ", "mind output ", "top-pain ", "prediction ", "desired state for prediction ", "handoff top-pain ",
    "wake requested ", "wake delivered ", "wake refused ",
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


class Coordinator:
    def __init__(self, config: RuntimeConfig):
        if config.judge is not None and config.batch_judge is not None:
            raise ValueError("select either a one-line judge or a batch judge")
        self.config = config
        self.home = config.home
        self.feed = Feed(self.home)
        self.policy = load_policy(config.policy)
        self.previous: dict[str, str] = {}
        self.filter_identity: dict[str, tuple[int, int] | None] = {}
        self.known_slugs = self._replay_known_slugs()
        self._lock_handle = None
        self.control_failures = self._run_controls()
    def _raw_judge(self, question: str, slug: str, pane: str, evidence: str, prediction: str | None = None) -> Judgment:
        if self.config.batch_judge is not None:
            return run_external_batch(self.config.batch_judge, (question,), slug, pane, evidence, prediction, timeout=self.policy.judge_timeout, question_texts={question: self.policy.question_text(question)})[question]
        if self.config.judge is not None:
            return run_external(self.config.judge, question, slug, pane, evidence, prediction, timeout=self.policy.judge_timeout, question_text=self.policy.question_text(question))
        if importlib.util.find_spec("laya") is None:
            return conservative_unknown(question, slug, pane, evidence, prediction, question_text=self.policy.question_text(question))
        from .laya_judge import judge as laya_judge
        request = document(question, slug, pane, evidence, prediction, question_text=self.policy.question_text(question))
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

    def acquire(self) -> None:
        path = self.home / ".runner.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_handle = path.open("a+")
        try:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"another coordinator holds {path}") from exc

    def close(self) -> None:
        if self._lock_handle:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            self._lock_handle.close()
            self._lock_handle = None

    def _judge(self, question: str, slug: str, pane: str, evidence: str, prediction: str | None = None) -> Judgment:
        failure = self.control_failures.get(question)
        if failure:
            return conservative_unknown(question, slug, pane, evidence, prediction, failure, question_text=self.policy.question_text(question))
        try:
            judgment = self._raw_judge(question, slug, pane, evidence, prediction)
        except Exception:
            return conservative_unknown(question, slug, pane, evidence, prediction, "judge failed", question_text=self.policy.question_text(question))
        return Judgment(judgment.question, judgment.probability, self.policy.classify(question, judgment.probability), judgment.reason, judgment.document)

    def _judge_batch(self, questions: tuple[str, ...], slug: str, pane: str, evidence: str, prediction: str | None = None) -> dict[str, Judgment]:
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
        return judgment_receipt(self.feed, judgment, slug, sequence, policy_version=self.policy.version, question_version=self.policy.question_version(judgment.question))

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
        current_slugs = set(discover(self.home))
        emitted: list[FeedEntry] = []
        entries = self.feed.entries()
        for slug in sorted(current_slugs - self.known_slugs):
            emitted.append(self.feed.append_runtime("mishe-tauftauf", f"top-pain {slug} live"))
        for slug in sorted(self.known_slugs - current_slugs):
            emitted.append(self.feed.append_runtime("mishe-tauftauf", f"top-pain {slug} absent"))
        self.known_slugs = current_slugs
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
            latest = next((entry.body for entry in reversed(self.feed.entries()) if entry.source == f"observation/{slug}"), None)
            if evidence == latest:
                continue
            judgment = self._judge("publish", slug, pane, evidence)
            self._receipt(judgment, slug, 0)
            if judgment.outcome != "no":
                emitted.append(self.feed.append_runtime(f"observation/{slug}", evidence))
        return emitted

    def assess_predictions(self) -> None:
        entries = self.feed.entries()
        predictions = replay_predictions(self.home, entries)
        assessed = {int(seq) for entry in entries if entry.source == "mishe-tauftauf" for seq, _ in ASSESS_RE.findall(entry.body)}
        for prediction in sorted(predictions.values(), key=lambda item: item.sequence):
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
        dispositions = {(int(seq), slug) for entry in entries if entry.source == "mishe-tauftauf" for seq, slug, _ in DISPOSITION_RE.findall(entry.body)}
        slugs = discover(self.home)
        for entry in entries:
            if is_bookkeeping(entry):
                continue
            for slug in slugs:
                if (entry.sequence, slug) in dispositions:
                    continue
                pane = self._pane(slug)
                relevance = self._judge("relevance", slug, pane, entry.body)
                self._receipt(relevance, slug, entry.sequence)
                if relevance.outcome == "no":
                    self.feed.append_runtime("mishe-tauftauf", f"entry {entry.sequence} for top-pain {slug}: irrelevant")
                    continue
                pending = pending_predictions(self.home, self.feed.entries(), slug)
                if pending:
                    prediction_text = "\n\n".join(item.body for item in pending)
                    gate = self._judge("continue-observing", slug, pane, entry.body, prediction_text)
                    self._receipt(gate, slug, entry.sequence)
                    disposition = "observe" if gate.outcome == "yes" else "wake"
                else:
                    gate = self._judge("desired-state-met", slug, pane, entry.body)
                    self._receipt(gate, slug, entry.sequence)
                    disposition = "observe" if gate.outcome == "yes" else "wake"
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
                result = subprocess.run([str(executable)], input=context.encode("utf-8"), capture_output=True, env=env)
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
            attempted = starts.get(key, set())
            finished = exits.get(key, set())
            if attempted and attempted <= finished:
                continue
            stimulus = by_sequence.get(sequence)
            if stimulus is None:
                continue
            if not attempted:
                pane = self._pane(slug)
                active_predictions = pending_predictions(self.home, self.feed.entries(), slug)
                if active_predictions:
                    prediction_text = "\n\n".join(item.body for item in active_predictions)
                    reassessment = self._judge(
                        "continue-observing", slug, pane, stimulus.body, prediction_text,
                    )
                else:
                    reassessment = self._judge("desired-state-met", slug, pane, stimulus.body)
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
            pending = pending_predictions(self.home, self.feed.entries())
            delay = self.config.interval
            if pending:
                delay = min(delay, max(0.0, (pending[0].check_at - now()).total_seconds()))
            time.sleep(max(0.05, delay))
