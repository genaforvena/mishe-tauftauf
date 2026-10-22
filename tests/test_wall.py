from datetime import datetime, timezone, timedelta
import hashlib
import multiprocessing
import json
import os
import subprocess
import pathlib
import pytest
from mishe_tauftauf import seed, post_check, observations
from mishe_tauftauf.feed import Feed


def setup_wall(home):
    (home / "coordination-mode.json").write_text(json.dumps({"mode": "wall", "until": (datetime.now(timezone.utc)+timedelta(hours=10)).isoformat()}))



def _concurrent_wall_outcome(home, evidence, role, barrier, results):
    from mishe_tauftauf import wall
    barrier.wait()
    try:
        wall.outcome(home, role, "accepted", f"Concurrent outcome by {role}.", evidence)
    except ValueError as exc:
        results.put(("refused", str(exc)))
    else:
        results.put(("published", role))

def _backdate_entry(home, sequence, timestamp):
    """Rewrite one feed entry header's timestamp, leaving its body untouched."""
    import re
    path = home / "chat.log"
    lines = path.read_text().splitlines(keepends=True)
    header = re.compile(r"((?:v1 )?%020d )\d{4}-\d{2}-\d{2}T[^\n]+Z( [A-Za-z0-9._/-]+ ::\n\Z)" % sequence)
    for index, line in enumerate(lines):
        match = header.fullmatch(line)
        if match:
            lines[index] = f"{match.group(1)}{timestamp}{match.group(2)}"
            break
    else:
        raise AssertionError(f"no feed header found for sequence {sequence}")
    path.write_text("".join(lines))

def test_wall_chat_does_not_call_receipt_reviewer(tmp_path, monkeypatch):
    setup_wall(tmp_path)
    def reject(*args, **kwargs):
        raise AssertionError("receipt reviewer was invoked")
    monkeypatch.setattr(post_check, "require", reject)
    assert Feed(tmp_path).append("genome", "Investigating two approaches; no patch yet.").sequence == 1


def test_planning_turn_settles_without_task_claim(tmp_path):
    setup_wall(tmp_path)
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nRead your wall.")
    note = tmp_path/"plan.md"
    note.write_text("I investigated two options. Next: prototype the smaller one.")
    assert "yield" in seed.yield_wake(tmp_path, "genome", wake.sequence, note, continue_task=True, result="verified")
    assert (tmp_path/"walls/genome.md").read_text() == note.read_text()
    assert seed._state(tmp_path, "genome")[1] is None


def test_wall_context_includes_only_monitored_role_walls(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    (tmp_path / "walls").mkdir(exist_ok=True)
    (tmp_path / "walls" / "genome.md").write_text("Genome plan")
    (tmp_path / "walls" / "health.md").write_text("Health plan")
    (tmp_path / "walls" / "operator.md").write_text("Operator position")
    (tmp_path / "walls" / "audit.md").write_text("Scratch audit")
    (tmp_path / "charters").mkdir()
    (tmp_path / "minds").mkdir()
    (tmp_path / "charters" / "resident.md").write_text("resident")
    (tmp_path / "minds" / "resident").write_text("#!/bin/sh\n")
    (tmp_path / "walls" / "resident.md").write_text("Resident plan")
    wall.message(tmp_path, "health", "genome", "Sample is ready; see artifacts/sample.md")
    text = wall.context(tmp_path, "genome")
    sections = [line for line in text.splitlines() if line.startswith("PEER WALL ")]
    assert sections == [
        f"PEER WALL health — reference only; never copy into your own wall",
        f"PEER WALL operator — reference only; never copy into your own wall",
        f"PEER WALL resident — reference only; never copy into your own wall",
    ]
    assert "Operator position" in text
    assert "Scratch audit" not in text
    assert "Sample is ready; see artifacts/sample.md" in text
def test_wall_write_rejects_uninitialized_reserved_home(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    # A typo'd --home must not silently raise a second site tree. A reserved
    # .mishe-* name inside a worktree needs the initialized layout first.
    stray = tmp_path / ".mishe-tauftauft"
    with pytest.raises(ValueError):
        wall.write(stray, "discover", "notes")
    assert not stray.exists()

def test_wall_write_rejects_oversized_wall(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    with pytest.raises(ValueError, match="exceeds its size limit"):
        wall.write(tmp_path, "genome", "x" * (wall.WALL_MAX_BYTES + 1))
    assert not (tmp_path / "walls" / "genome.md").exists()


def test_wall_write_rejects_too_many_lines(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    with pytest.raises(ValueError, match="exceeds its size limit"):
        wall.write(tmp_path, "genome", "line\n" * (wall.WALL_MAX_LINES + 1))
    assert not (tmp_path / "walls" / "genome.md").exists()


def test_wall_limit_is_configurable_and_enforced_at_the_bound(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    (tmp_path / "coordination-mode.json").write_text(json.dumps({"mode": "wall", "wall_max_bytes": 32}))
    wall.write(tmp_path, "genome", "y" * 32)
    assert (tmp_path / "walls" / "genome.md").read_text() == "y" * 32
    with pytest.raises(ValueError, match="exceeds its size limit"):
        wall.write(tmp_path, "genome", "y" * 33)
    assert (tmp_path / "walls" / "genome.md").read_text() == "y" * 32


def test_wall_write_audit_log_records_invocation(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    notes = tmp_path / "NOTES"
    notes.write_text("Audit test notes")
    wall.write(tmp_path, "genome", "Audit test notes", source_path=str(notes))
    audit_path = tmp_path / "logs" / "wall-write-audit.jsonl"
    assert audit_path.is_file()
    entry = json.loads(audit_path.read_text())
    assert entry["role"] == "genome"
    assert entry["source"] == str(notes)
    assert entry["wall"].endswith("walls/genome.md")
    assert "ts" in entry and "mtime" in entry


def test_wall_write_audit_log_works_without_source_path(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    wall.write(tmp_path, "genome", "No source path")
    audit_path = tmp_path / "logs" / "wall-write-audit.jsonl"
    assert audit_path.is_file()
    entry = json.loads(audit_path.read_text())
    assert entry["role"] == "genome"
    assert entry["source"] is None


def test_wall_write_rejects_peer_wall_copy(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    wall.write(tmp_path, "genome", "Wake 1494 verified. Phone light channel regular.")
    with pytest.raises(ValueError, match="identical to peer wall genome"):
        wall.write(tmp_path, "witness", "Wake 1494 verified. Phone light channel regular.")
    assert not (tmp_path / "walls" / "witness.md").exists()
    assert (tmp_path / "walls" / "genome.md").read_text().startswith("Wake 1494")


def test_wall_write_allows_unchanged_own_wall_rewrite(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    wall.write(tmp_path, "genome", "Steady state; nothing new.")
    wall.write(tmp_path, "genome", "Steady state; nothing new.")
    assert (tmp_path / "walls" / "genome.md").read_text() == "Steady state; nothing new."


def test_wall_write_projects_the_launcher_handoff(tmp_path):
    """The wall and its launcher-facing projection are one document.

    runtime._context reads handoffs/<role>.md as CURRENT HANDOFF for the next
    invocation, so a wall edit that leaves the projection behind hands that
    invocation the previous wake's notes.
    """
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    wall.write(tmp_path, "genome", "Plate: resume the margin measurement.")
    assert (tmp_path / "handoffs" / "genome.md").read_text() == "Plate: resume the margin measurement."
    wall.write(tmp_path, "genome", "Plate: park it; evidence is in.")
    assert (tmp_path / "handoffs" / "genome.md").read_text() == "Plate: park it; evidence is in."


def test_same_wake_resettles_after_a_direct_wall_edit(tmp_path):
    """A mind may edit its wall and still settle the wake it is holding.

    android-body's genome wake 35129 stayed pending 07:23-08:03Z on 2026-10-11:
    the wall wrote new notes directly, the handoff projection kept the settled
    wake's text, and the re-settle's file comparison raised for good. Settling
    the same wake after a direct wall edit must not wedge the mind.
    """
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nRead your wall.")
    wall.settle(tmp_path, "genome", wake.sequence, "First notes for the held wake.", False, "changed")
    wall.write(tmp_path, "genome", "Corrected notes for the held wake.", source_path=None)
    wall.settle(tmp_path, "genome", wake.sequence, "Corrected notes for the held wake.", False, "changed")
    assert (tmp_path / "handoffs" / "genome.md").read_text() == "Corrected notes for the held wake."
    assert (tmp_path / "walls" / "genome.md").read_text() == "Corrected notes for the held wake."


def test_peer_copy_guard_ignores_trailing_blank_lines(tmp_path):
    """The paste class cannot pass by ending on a different blank-line count."""
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    wall.write(tmp_path, "genome", "Same evidence, new framing.\n\n\n")
    with pytest.raises(ValueError, match="identical to peer wall genome"):
        wall.write(tmp_path, "witness", "Same evidence, new framing.")


def test_peer_copy_guard_refuses_a_paste_without_trailing_newlines(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    wall.write(tmp_path, "genome", "Same evidence, new framing.")
    with pytest.raises(ValueError, match="identical to peer wall genome"):
        wall.write(tmp_path, "witness", "Same evidence, new framing.\n")


def test_peer_copy_guard_accepts_a_paste_differing_beyond_trailing_lines(tmp_path):
    """Only trailing newlines are normalized: an interior difference is own work."""
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    wall.write(tmp_path, "genome", "Line one.\n\nLine two.")
    wall.write(tmp_path, "witness", "Line one.\nLine two.\n\n")
    assert (tmp_path / "walls" / "witness.md").read_text() == "Line one.\nLine two.\n\n"


def test_wall_write_audit_records_content_digest(tmp_path):
    """The audit digest is the normalized document, matching the peer-copy rule."""
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    wall.write(tmp_path, "genome", "Digest probe notes.\n\n")
    entry = json.loads((tmp_path / "logs" / "wall-write-audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert entry["digest"] == hashlib.sha256("Digest probe notes.".encode("utf-8")).hexdigest()


def test_wall_write_projects_the_handoff_beside_the_wall(tmp_path):
    """The handoff projection must carry the same bytes as the wall it mirrors."""
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    wall.write(tmp_path, "genome", "Wake 8405 notes; handoff must match.")
    assert (tmp_path / "handoffs" / "genome.md").read_text(encoding="utf-8") == "Wake 8405 notes; handoff must match."


def test_wall_write_replaces_a_stale_handoff_projection(tmp_path):
    """A wall write must not leave the previous wake's handoff in place."""
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    (tmp_path / "handoffs").mkdir()
    (tmp_path / "handoffs" / "genome.md").write_text("previous wake notes", encoding="utf-8")
    wall.write(tmp_path, "genome", "current wake notes")
    assert (tmp_path / "handoffs" / "genome.md").read_text(encoding="utf-8") == "current wake notes"


def test_wall_write_refuses_stale_peer_document_from_audit_history(tmp_path):
    """A peer's superseded wall cannot pass the live-wall comparison unnoticed."""
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    wall.write(tmp_path, "witness", "Wake 7899 findings; later superseded.")
    wall.write(tmp_path, "witness", "Wake 8219 findings; current.")
    with pytest.raises(ValueError, match="identical to a document witness wrote"):
        wall.write(tmp_path, "discover", "Wake 7899 findings; later superseded.")
    assert not (tmp_path / "walls" / "discover.md").exists()
    assert (tmp_path / "walls" / "witness.md").read_text() == "Wake 8219 findings; current."


def test_peer_copy_refusal_audits_to_wall_write_audit_jsonl(tmp_path):
    """A refused peer-wall paste leaves a durable record, not only pane stderr.

    The paste class clobbered three walls on 2026-10-10 and one on 2026-10-11,
    so the guard's refusals are the recurrence record for that class: a refusal
    visible only in the caller's pane cannot be counted or routed. The record
    carries the same shape as the stale-anchor refusal (role, source, wall,
    both mtimes, reason).
    """
    setup_wall(tmp_path)
    from mishe_tauftauf import cli
    (tmp_path / "walls").mkdir()
    (tmp_path / "walls" / "genome.md").write_text("Same evidence, new framing.\n", encoding="utf-8")
    notes = tmp_path / "notes.md"
    notes.write_text("Same evidence, new framing.\n", encoding="utf-8")
    code = cli.main(["--home", str(tmp_path), "wall", "write", "--owner", "witness", "--file", str(notes)])
    assert code == 2
    entry = json.loads((tmp_path / "logs" / "wall-write-audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert entry["reason"] == "peer-copy"
    assert entry["role"] == "witness"
    assert entry["source"] == str(notes)
    assert entry["wall"] == str(tmp_path / "walls" / "witness.md")
    assert "ts" in entry
    assert "pid" in entry
    assert not (tmp_path / "walls" / "witness.md").exists()


def test_peer_history_copy_refusal_audits_its_own_reason(tmp_path):
    """A refused superseded-peer paste is recorded as its own refusal class."""
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    wall.write(tmp_path, "witness", "Wake 7899 findings; later superseded.")
    wall.write(tmp_path, "witness", "Wake 8219 findings; current.")
    with pytest.raises(ValueError, match="identical to a document witness wrote"):
        wall.write(tmp_path, "discover", "Wake 7899 findings; later superseded.")
    entry = json.loads((tmp_path / "logs" / "wall-write-audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert entry["reason"] == "peer-copy-history"
    assert entry["role"] == "discover"
    assert entry["wall"] == str(tmp_path / "walls" / "discover.md")


def test_wall_write_allows_own_unchanged_rewrite_against_history(tmp_path):
    """Own earlier digests must not block rewriting a wall with unchanged content."""
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    wall.write(tmp_path, "witness", "Steady plan carried forward.")
    wall.write(tmp_path, "witness", "Steady plan carried forward.")
    assert (tmp_path / "walls" / "witness.md").read_text() == "Steady plan carried forward."


def test_wall_write_resolves_bare_notes_to_role_staging_anchor(tmp_path, monkeypatch):
    """A bare notes name resolves to the writer's own staging anchor only.

    The shared walls/NOTES and a same-named file in the working directory are
    both ignored: the anchor is role-scoped so one role can never read another
    role's staged notes (class: genome's content on docs' wall, 2026-10-11).
    """
    setup_wall(tmp_path)
    from mishe_tauftauf import cli
    (tmp_path / "walls").mkdir()
    (tmp_path / "walls" / "NOTES").write_text("genome's shared staging notes", encoding="utf-8")
    (tmp_path / "walls" / "docs-NOTES").write_text("docs' own staging notes", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "NOTES").write_text("same-named file in the working directory", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    code = cli.main(["--home", str(tmp_path), "wall", "write", "--owner", "docs", "--file", "NOTES"])
    assert code == 0
    assert (tmp_path / "walls" / "docs.md").read_text(encoding="utf-8") == "docs' own staging notes"
    entry = json.loads((tmp_path / "logs" / "wall-write-audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert entry["source"] == str(tmp_path / "walls" / "docs-NOTES")


def test_wall_write_fails_closed_without_role_staging_anchor(tmp_path, monkeypatch):
    """Without the writer's own anchor the write is refused, not re-anchored.

    Falling back to a same-named file in the shared directory or the working
    directory is the contamination vector; the command must fail loudly.
    """
    setup_wall(tmp_path)
    from mishe_tauftauf import cli
    (tmp_path / "walls").mkdir()
    (tmp_path / "walls" / "NOTES").write_text("another role's staged notes", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "NOTES").write_text("same-named file in the working directory", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    code = cli.main(["--home", str(tmp_path), "wall", "write", "--owner", "docs", "--file", "NOTES"])
    assert code == 2
    assert not (tmp_path / "walls" / "docs.md").exists()


def test_wall_write_refuses_a_stale_staging_anchor_that_would_regress_the_wall(tmp_path):
    """A bare-anchor write older than the wall fails closed instead of regressing the plate.

    An anchor left un-consumed by an earlier wake is read as this wake's plate:
    the write would overwrite the newer wall and report success, losing the
    wake's findings (class: witness plate reverted to a wake 56136 draft on
    2026-10-16 from an anchor last staged at 02:10Z; reproduction
    ``artifacts/witness-wake58696-anchor-clobber-repro.py``).
    """
    setup_wall(tmp_path)
    from mishe_tauftauf import cli
    (tmp_path / "walls").mkdir()
    anchor = tmp_path / "walls" / "genome-NOTES"
    anchor.write_text("PLATE A: leaked from an earlier wake.\n", encoding="utf-8")
    wall_path = tmp_path / "walls" / "genome.md"
    wall_path.write_text("PLATE B: current plate.\n", encoding="utf-8")
    os.utime(anchor, (1_700_000_000, 1_700_000_000))
    os.utime(wall_path, (1_700_000_100, 1_700_000_100))
    code = cli.main(["--home", str(tmp_path), "wall", "write", "--owner", "genome", "--file", "NOTES"])
    assert code == 2
    assert wall_path.read_text(encoding="utf-8") == "PLATE B: current plate.\n"

def test_wall_write_refusal_audits_to_wall_write_audit_jsonl(tmp_path):
    """A refused stale-anchor write leaves a durable audit record.

    The refusal is visible only to the mind's pane (stdout/stderr); without a
    durable log line the same shape as the stale-module class (a live defect
    with no recurrence record) recurs silently. This test drives the real
    refusal and verifies that ``_audit_refusal`` writes to
    ``logs/wall-write-audit.jsonl`` with role, source, wall, both mtimes, and
    the refusal reason.
    """
    setup_wall(tmp_path)
    from mishe_tauftauf import cli
    (tmp_path / "walls").mkdir()
    anchor = tmp_path / "walls" / "genome-NOTES"
    anchor.write_text("PLATE A: leaked from an earlier wake.\n", encoding="utf-8")
    wall_path = tmp_path / "walls" / "genome.md"
    wall_path.write_text("PLATE B: current plate.\n", encoding="utf-8")
    os.utime(anchor, (1_700_000_000, 1_700_000_000))
    os.utime(wall_path, (1_700_000_100, 1_700_000_100))
    code = cli.main(["--home", str(tmp_path), "wall", "write", "--owner", "genome", "--file", "NOTES"])
    assert code == 2
    audit_path = tmp_path / "logs" / "wall-write-audit.jsonl"
    assert audit_path.is_file()
    entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["role"] == "genome"
    assert entry["source"] == str(anchor)
    assert entry["wall"] == str(wall_path)
    assert entry["reason"] == "stale-staging-anchor"
    assert entry["source_mtime"] == "2023-11-14T22:13:20+00:00"
    assert entry["wall_mtime"] == "2023-11-14T22:15:00+00:00"
    assert "ts" in entry
    assert "pid" in entry


def test_wall_write_accepts_a_staging_anchor_newer_than_the_wall(tmp_path):
    """A freshly staged anchor is this wake's plate and is written."""
    setup_wall(tmp_path)
    from mishe_tauftauf import cli
    (tmp_path / "walls").mkdir()
    wall_path = tmp_path / "walls" / "genome.md"
    wall_path.write_text("old plate\n", encoding="utf-8")
    anchor = tmp_path / "walls" / "genome-NOTES"
    anchor.write_text("new plate staged this wake\n", encoding="utf-8")
    os.utime(wall_path, (1_700_000_000, 1_700_000_000))
    os.utime(anchor, (1_700_000_100, 1_700_000_100))
    code = cli.main(["--home", str(tmp_path), "wall", "write", "--owner", "genome", "--file", "NOTES"])
    assert code == 0
    assert wall_path.read_text(encoding="utf-8") == "new plate staged this wake\n"

def test_seed_yield_refuses_a_stale_staging_anchor_that_would_regress_the_wall(tmp_path):
    """A bare-anchor settle older than a directly edited wall fails closed.

    The doctrine names the plate as ``walls/<role>.md``; a mind that edits it
    directly and then settles with the previous wake's staged anchor would
    otherwise overwrite the newer wall with no error and lose the wake's
    findings (class: android-body discover, wall reverted to a stale draft
    2026-10-14; reproduction ``artifacts/discover-wake39796-anchor-clobber-repro.py``).
    """
    setup_wall(tmp_path)
    from mishe_tauftauf import cli
    (tmp_path / "walls").mkdir()
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nRead your wall.")
    anchor = tmp_path / "walls" / "genome-NOTES"
    anchor.write_text("PLATE A: previous wake.\n", encoding="utf-8")
    wall_path = tmp_path / "walls" / "genome.md"
    wall_path.write_text("PLATE B: edited directly this wake.\n", encoding="utf-8")
    os.utime(anchor, (1_700_000_000, 1_700_000_000))
    os.utime(wall_path, (1_700_000_100, 1_700_000_100))
    code = cli.main(["--home", str(tmp_path), "seed", "yield", "--slug", "genome",
                     "--wake", str(wake.sequence), "--file", "NOTES", "--result", "changed"])
    assert code == 2
    assert wall_path.read_text(encoding="utf-8") == "PLATE B: edited directly this wake.\n"
    assert seed._state(tmp_path, "genome")[1] == wake.sequence


def test_seed_yield_accepts_a_staging_anchor_newer_than_the_wall(tmp_path):
    """Staged notes newer than the wall are the mind's current plate; settle writes them."""
    setup_wall(tmp_path)
    from mishe_tauftauf import cli
    (tmp_path / "walls").mkdir()
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nRead your wall.")
    wall_path = tmp_path / "walls" / "genome.md"
    wall_path.write_text("old plate\n", encoding="utf-8")
    anchor = tmp_path / "walls" / "genome-NOTES"
    anchor.write_text("new plate staged this wake\n", encoding="utf-8")
    os.utime(wall_path, (1_700_000_000, 1_700_000_000))
    os.utime(anchor, (1_700_000_100, 1_700_000_100))
    code = cli.main(["--home", str(tmp_path), "seed", "yield", "--slug", "genome",
                     "--wake", str(wake.sequence), "--file", "NOTES", "--result", "changed"])
    assert code == 0
    assert wall_path.read_text(encoding="utf-8") == "new plate staged this wake\n"


def test_seed_yield_accepts_a_staging_anchor_matching_the_wall(tmp_path):
    """The documented ``wall write`` then ``seed yield`` flow keeps the anchor equal to the wall."""
    setup_wall(tmp_path)
    from mishe_tauftauf import cli
    (tmp_path / "walls").mkdir()
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nRead your wall.")
    wall_path = tmp_path / "walls" / "genome.md"
    anchor = tmp_path / "walls" / "genome-NOTES"
    wall_path.write_text("same plate\n", encoding="utf-8")
    anchor.write_text("same plate\n", encoding="utf-8")
    os.utime(anchor, (1_700_000_000, 1_700_000_000))
    os.utime(wall_path, (1_700_000_100, 1_700_000_100))
    code = cli.main(["--home", str(tmp_path), "seed", "yield", "--slug", "genome",
                     "--wake", str(wake.sequence), "--file", "NOTES", "--result", "changed"])
    assert code == 0
    assert wall_path.read_text(encoding="utf-8") == "same plate\n"


def test_seed_yield_accepts_an_explicit_notes_path_older_than_the_wall(tmp_path):
    """An explicit ``--file`` path outside the anchor is the caller's deliberate choice."""
    setup_wall(tmp_path)
    from mishe_tauftauf import cli
    (tmp_path / "walls").mkdir()
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nRead your wall.")
    wall_path = tmp_path / "walls" / "genome.md"
    wall_path.write_text("current plate\n", encoding="utf-8")
    staged = tmp_path / "artifacts"
    staged.mkdir()
    notes = staged / "notes.md"
    notes.write_text("deliberate older notes\n", encoding="utf-8")
    os.utime(notes, (1_700_000_000, 1_700_000_000))
    os.utime(wall_path, (1_700_000_100, 1_700_000_100))
    code = cli.main(["--home", str(tmp_path), "seed", "yield", "--slug", "genome",
                     "--wake", str(wake.sequence), "--file", str(notes), "--result", "changed"])
    assert code == 0
    assert wall_path.read_text(encoding="utf-8") == "deliberate older notes\n"


def test_wall_write_explicit_relative_path_resolves_from_cwd(tmp_path, monkeypatch):
    """A relative --file with a directory component keeps its cwd meaning."""
    setup_wall(tmp_path)
    from mishe_tauftauf import cli
    (tmp_path / "walls").mkdir()
    (tmp_path / "walls" / "genome-NOTES").write_text("staged anchor", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "artifacts").mkdir()
    (elsewhere / "artifacts" / "notes.md").write_text("explicit relative notes", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    code = cli.main(["--home", str(tmp_path), "wall", "write", "--owner", "genome",
                     "--file", "artifacts/notes.md"])
    assert code == 0
    assert (tmp_path / "walls" / "genome.md").read_text(encoding="utf-8") == "explicit relative notes"


def test_wall_write_accepts_absolute_notes_path(tmp_path, monkeypatch):
    """An absolute staging path is never re-anchored."""
    setup_wall(tmp_path)
    from mishe_tauftauf import cli
    staged = tmp_path / "staged-notes.md"
    staged.write_text("absolute notes", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code = cli.main(["--home", str(tmp_path), "wall", "write", "--owner", "genome", "--file", str(staged)])
    assert code == 0
    assert (tmp_path / "walls" / "genome.md").read_text(encoding="utf-8") == "absolute notes"


def test_wall_dm_resolves_bare_file_to_sender_staging_anchor(tmp_path, monkeypatch):
    """The dm message path reads the sender's own staging anchor."""
    setup_wall(tmp_path)
    from mishe_tauftauf import cli
    from mishe_tauftauf.feed import Feed
    (tmp_path / "walls").mkdir()
    (tmp_path / "walls" / "MESSAGE").write_text("shared message text", encoding="utf-8")
    (tmp_path / "walls" / "health-MESSAGE").write_text("health's own message text", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "MESSAGE").write_text("same-named file in the working directory", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    code = cli.main(["--home", str(tmp_path), "wall", "dm", "--source", "health", "--to", "genome",
                     "--file", "MESSAGE"])
    assert code == 0
    entry = Feed(tmp_path).entries()[-1]
    assert entry.source == "health" and "health's own message text" in entry.body


def test_wall_dm_consumes_staging_anchor_after_send(tmp_path):
    """After a successful dm send from the staging anchor, the anchor is consumed.

    A second send with the same bare name must fail because the anchor no longer
    exists — this prevents the stale-anchor re-emission defect (DM 41642).
    """
    setup_wall(tmp_path)
    from mishe_tauftauf import cli
    (tmp_path / "walls").mkdir()
    (tmp_path / "walls" / "health-MESSAGE").write_text("health's staged message", encoding="utf-8")
    code = cli.main(["--home", str(tmp_path), "wall", "dm", "--source", "health", "--to", "genome",
                     "--file", "MESSAGE"])
    assert code == 0
    anchor = tmp_path / "walls" / "health-MESSAGE"
    assert not anchor.exists(), "staging anchor must be consumed after successful send"
    code2 = cli.main(["--home", str(tmp_path), "wall", "dm", "--source", "health", "--to", "genome",
                      "--file", "MESSAGE"])
    assert code2 != 0, "second send must fail — anchor was consumed"


def test_wall_dm_race_restage_mid_send_preserves_newer_anchor(tmp_path, monkeypatch, capsys):
    """If the staging anchor is restaged mid-send, the newer bytes are preserved.

    Genome DM 41746 identified the residual race: between read and unlink, a
    restage of the same anchor path would unlink the newer bytes, not the ones
    actually sent. The guard verifies inode+mtime before consuming.
    """
    setup_wall(tmp_path)
    from mishe_tauftauf import cli, wall
    (tmp_path / "walls").mkdir()
    anchor = tmp_path / "walls" / "health-MESSAGE"
    anchor.write_text("original staged message", encoding="utf-8")

    original_message = wall.message
    def restaging_send(*args, **kwargs):
        """Simulate a concurrent restage during the send."""
        anchor.write_text("restaged during send", encoding="utf-8")
        return original_message(*args, **kwargs)
    monkeypatch.setattr(wall, "message", restaging_send)

    code = cli.main(["--home", str(tmp_path), "wall", "dm", "--source", "health", "--to", "genome",
                     "--file", "MESSAGE"])
    assert code == 0, "send succeeds even when anchor is restaged mid-flight"
    assert anchor.exists(), "restaged anchor must survive — newer bytes preserved"
    assert anchor.read_text(encoding="utf-8") == "restaged during send"
    captured = capsys.readouterr()
    assert "not consumed" in captured.out
def test_wall_outcome_resolves_bare_notes_to_role_staging_anchor(tmp_path):
    """The outcome path reads the recording role's own staging anchor."""
    setup_wall(tmp_path)
    from mishe_tauftauf import cli
    (tmp_path / "walls").mkdir()
    (tmp_path / "walls" / "NOTES").write_text("another role's staged notes", encoding="utf-8")
    (tmp_path / "walls" / "genome-NOTES").write_text("genome's own outcome notes", encoding="utf-8")
    evidence = tmp_path / "artifacts" / "evidence.txt"
    evidence.parent.mkdir()
    evidence.write_text("evidence bytes", encoding="utf-8")
    code = cli.main(["--home", str(tmp_path), "wall", "outcome", "--owner", "genome",
                     "--kind", "accepted", "--file", "NOTES", "--evidence", str(evidence)])
    assert code == 0
    entry = Feed(tmp_path).entries()[-1]
    assert "genome's own outcome notes" in entry.body



def test_wall_is_visible_without_hiding_failed_sensor(tmp_path, monkeypatch):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    wall.write(tmp_path, "health", "Planning an investigation; no repair yet.")
    monkeypatch.setattr(observations, "run_renderer", lambda *a, **k: observations.RenderedPain("health", "STATE: RED sensor failed\n", False))
    rendered = observations.compose_frame(tmp_path, "health")
    assert not rendered.ok
    assert "STATE: RED sensor failed" in rendered.body and "Planning an investigation" in rendered.body



def test_check_report_marks_stale_by_mtime_without_changing_report(tmp_path, monkeypatch):
    from mishe_tauftauf import observations
    path = tmp_path / "observations" / "genome"
    path.parent.mkdir()
    path.write_text("PASS pinned sha=old\n")
    now = 10_000
    monkeypatch.setattr(observations.time, "time", lambda: now)

    os.utime(path, (now - 900, now - 900))
    assert observations.check_report(tmp_path, "genome") == "SYSTEM ZERO\nPASS pinned sha=old\n"

    os.utime(path, (now - 901, now - 901))
    assert observations.check_report(tmp_path, "genome") == (
        "SYSTEM ZERO\nSTALE — check report exceeds 900 seconds\nPASS pinned sha=old\n"
    )


def test_check_report_missing_mtime_is_stale(tmp_path, monkeypatch):
    from mishe_tauftauf import observations
    path = tmp_path / "observations" / "genome"
    path.parent.mkdir()
    path.write_text("PASS old\n")
    from pathlib import Path
    original_stat = Path.stat

    def failing_stat(self, *args, **kwargs):
        if self == path:
            raise OSError("mtime unavailable")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", failing_stat)
    assert observations.check_report(tmp_path, "genome") == (
        "SYSTEM ZERO\nSTALE — check report exceeds 900 seconds\nPASS old\n"
    )


def test_check_report_future_mtime_is_stale(tmp_path, monkeypatch):
    """A report mtime ahead of the wall clock is not evidence of freshness."""
    from mishe_tauftauf import observations
    path = tmp_path / "observations" / "genome"
    path.parent.mkdir()
    path.write_text("PASS pinned sha=old\n")
    now = 10_000
    monkeypatch.setattr(observations.time, "time", lambda: now)

    os.utime(path, (now + 60, now + 60))
    assert observations.check_report(tmp_path, "genome") == (
        "SYSTEM ZERO\nSTALE — check report timestamp is ahead of the wall clock\nPASS pinned sha=old\n"
    )

    os.utime(path, (now, now))
    assert observations.check_report(tmp_path, "genome") == "SYSTEM ZERO\nPASS pinned sha=old\n"


def test_watcher_future_heartbeat_is_stale(tmp_path):
    """A heartbeat timestamp ahead of the wall clock cannot read live."""
    from mishe_tauftauf import observations
    checks = tmp_path / "checks"
    checks.mkdir()
    heartbeat = checks / "silence-heartbeat.json"

    heartbeat.write_text(json.dumps({"at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}))
    assert observations._watcher(tmp_path) == "stale"

    heartbeat.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat()}))
    assert observations._watcher(tmp_path) == "live"

def test_pane_reads_the_shared_feed_once(tmp_path, monkeypatch):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall

    calls = []
    original = Feed.entries

    def counting(self, **kwargs):
        calls.append(self.home)
        return original(self, **kwargs)

    monkeypatch.setattr(Feed, "entries", counting)
    wall.pane(tmp_path, "genome")
    assert len(calls) == 1, "a pane frame must not rescan the whole feed per section"


def test_feed_read_paths_take_a_shared_lock(tmp_path, monkeypatch):
    setup_wall(tmp_path)
    Feed(tmp_path).append("genome", "Shared read lock probe with a real entry.")
    import fcntl

    modes = []
    original = fcntl.flock

    def recording(fd, mode):
        modes.append(mode)
        return original(fd, mode)

    monkeypatch.setattr(fcntl, "flock", recording)
    Feed(tmp_path).entries()
    Feed(tmp_path).tail_sequence()
    assert modes and all(mode == fcntl.LOCK_SH for mode in modes), \
        "concurrent readers must not serialize behind an exclusive lock"


def test_expired_trial_stops_new_wakes(tmp_path):
    from mishe_tauftauf import wall
    (tmp_path/"coordination-mode.json").write_text(json.dumps({"mode": "wall", "until": (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}))
    assert "ended" in wall.tick(tmp_path, "session", "genome", 300)
    assert not Feed(tmp_path).entries()


def test_expired_trial_can_recover_existing_turn_without_new_wake(tmp_path, monkeypatch):
    from mishe_tauftauf import wall, tmux
    (tmp_path/"coordination-mode.json").write_text(json.dumps({"mode": "wall", "until": (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}))
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nRead wall.")
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    sent = []
    monkeypatch.setattr(seed, "_send", lambda target, text: sent.append(text))
    assert wall.tick(tmp_path, "session", "genome", 300).startswith("wake ")
    assert "wake window ended" in sent[-1]
    assert len([e for e in Feed(tmp_path).entries() if e.body.startswith("seed wake ")]) == 1


def test_invalid_mode_refuses_silently_switching_coordination(tmp_path):
    from mishe_tauftauf import wall
    (tmp_path/"coordination-mode.json").write_text("broken")
    with pytest.raises(ValueError, match="coordination mode"):
        wall.enabled(tmp_path)


def test_addressed_message_wakes_stable_mind_without_ledger(tmp_path, monkeypatch):
    from mishe_tauftauf import wall, tmux, dashboard
    setup_wall(tmp_path)
    sent = []
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(tmux, "_pane_stopped_or_dead", lambda *a: False)
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    monkeypatch.setattr(tmux, "capture_raw", lambda *a: f"-- pane live {stamp} · refresh 5s · ticks every frame --\n")
    monkeypatch.setattr(dashboard, "read", lambda *a: ("STATE: GREEN steady\n", True))
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    monkeypatch.setattr(seed, "_send", lambda target, text: sent.append(text))
    first = int(wall.tick(tmp_path, "session", "genome", 3600).split()[-1])
    assert "Wall coordination is canonical" in sent[-1]
    note = tmp_path / "note"
    note.write_text("Planning done. Waiting for sample.")
    seed.yield_wake(tmp_path, "genome", first, note, result="verified")
    Feed(tmp_path).append("seed", f"seed clear genome after={first}\nRotated.")
    assert "waiting" in wall.tick(tmp_path, "session", "genome", 3600)
    wall.message(tmp_path, "health", "genome", "New sample available.")
    assert wall.tick(tmp_path, "session", "genome", 3600).startswith("wake ")
    assert "New sample available" in sent[-1]
    assert "unsettled" in wall.tick(tmp_path, "session", "genome", 3600)
    assert len(sent) == 2


def test_failed_command_with_same_text_wakes_after_settlement(tmp_path, monkeypatch):
    from mishe_tauftauf import wall, tmux, dashboard
    setup_wall(tmp_path)
    sent = []
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(tmux, "_pane_stopped_or_dead", lambda *a: False)
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    monkeypatch.setattr(tmux, "capture_raw", lambda *a: f"-- pane live {stamp} · refresh 5s · ticks every frame --\n")
    ok = [True]
    monkeypatch.setattr(dashboard, "read", lambda *a: ("STATE: GREEN steady\n", ok[0]))
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    monkeypatch.setattr(seed, "_send", lambda target, text: sent.append(text))
    first = int(wall.tick(tmp_path, "session", "genome", 3600).split()[-1])
    ok[0] = False
    assert "unsettled" in wall.tick(tmp_path, "session", "genome", 3600)
    note = tmp_path / "note"
    note.write_text("Turn completed; inspect failed command next.")
    seed.yield_wake(tmp_path, "genome", first, note, result="verified")
    Feed(tmp_path).append("seed", f"seed clear genome after={first}\nRotated.")
    assert wall.tick(tmp_path, "session", "genome", 3600).startswith("wake ")
    assert len(sent) == 2


def test_docs_pane_reads_edits_and_reports_missing_page(tmp_path):
    from mishe_tauftauf.wall_view import render
    home = tmp_path / "site"
    home.mkdir()
    docs = tmp_path / "docs"
    docs.mkdir()
    (home / "coordination-mode.json").write_text(json.dumps({"mode": "wall", "docs_document": "docs/mesh.md"}))
    page = docs / "mesh.md"
    page.write_text("Mesh helps minds observe their work.")
    assert "Mesh helps" in render(home, "docs")
    page.write_text("Mesh is a shared practice.")
    assert "shared practice" in render(home, "docs")
    assert "Mesh helps" not in render(home, "docs")
    page.unlink()
    assert "STATE: UNKNOWN" in render(home, "docs")


def test_wall_view_dispatches_witness_and_marks_unproduced_roles(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_view

    called = []
    monkeypatch.setattr(wall_view.seed_witness_view, "render", lambda home: called.append(home) or "WITNESS\n")
    assert wall_view.render(tmp_path, "witness") == "WITNESS\n"
    assert called == [tmp_path]
    for role in ("genome", "research-methods"):
        assert "REPORT: NOT PRODUCED" in wall_view.render(tmp_path, role)

def test_compose_frame_omits_legacy_reports_for_reportless_roles(tmp_path, monkeypatch):
    setup_wall(tmp_path)
    from mishe_tauftauf import observations, wall
    from mishe_tauftauf.observations import RenderedPain
    reports = tmp_path / "observations"
    reports.mkdir()
    for role in ("genome", "research-methods"):
        (reports / role).write_text("STALE LEGACY VERDICT\n")
        monkeypatch.setattr(
            observations, "run_renderer",
            lambda _home, slug, _timeout, **k: RenderedPain(slug, "REPORT: NOT PRODUCED\n", True),
        )
        monkeypatch.setattr(wall, "pane", lambda *_args: "")
        frame = observations.compose_frame(tmp_path, role).body
        assert "REPORT: NOT PRODUCED" in frame
        assert "STALE LEGACY VERDICT" not in frame

def test_health_dashboard_uses_user_runtime_and_preserves_bus_unknown(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_view

    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "services.json").write_text(json.dumps(["health.service"]))
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    expected_runtime = f"/run/user/{os.getuid()}"

    def systemctl_with_runtime(command, **kwargs):
        if kwargs.get("env", {}).get("XDG_RUNTIME_DIR") == expected_runtime:
            return subprocess.CompletedProcess(
                command, 0,
                "Id=health.service\nActiveState=active\nSubState=running\nNRestarts=0\n",
                "")
        return subprocess.CompletedProcess(
            command, 1, "", "Failed to connect to bus: No medium found")

    monkeypatch.setattr(wall_view.subprocess, "run", systemctl_with_runtime)
    monkeypatch.setattr(wall_view, "ci_line", lambda _home: "CI: PASS")
    monkeypatch.setattr(wall_view, "renderer_bytes_lines", lambda h: (
        ["RENDERER BYTES: GREEN pin=x renderers=12"], "GREEN"))
    healthy = wall_view.render(home, "health")
    assert "SERVICES: GREEN — 1 listed units active/running, 0 restarts" in healthy
    assert "STATE: GREEN — listed services running" in healthy

    def systemctl_failed(command, **_kwargs):
        return subprocess.CompletedProcess(
            command, 1, "", "Failed to connect to bus: No medium found")

    monkeypatch.setattr(wall_view.subprocess, "run", systemctl_failed)
    unavailable = wall_view.render(home, "health")
    assert "STATE: UNKNOWN — services unavailable: Failed to connect to bus: No medium found" in unavailable


def test_empty_service_manifest_prints_no_phantom_service_row(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_view

    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "services.json").write_text(json.dumps([]), encoding="utf-8")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("systemctl show called for an empty manifest")

    monkeypatch.setattr(wall_view.subprocess, "run", unexpected)
    lines, roots, note = wall_view._service_block(home)
    assert lines == []
    assert roots == {}
    assert note == "RED — service failure or empty manifest"


def test_health_dashboard_flags_a_frozen_renderer_lease(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_view

    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "windows.json").write_text(json.dumps(["health"]), encoding="utf-8")
    (home / "health" / "services.json").write_text(json.dumps(["health.service"]), encoding="utf-8")
    (home / "top-pains").mkdir()
    (home / "top-pains" / "health").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("MISHE_SEED_SESSION", "sess")
    capture = {"stdout": ""}

    def run(argv, **_kwargs):
        if argv[0] == "tmux" and "capture-pane" in argv:
            return subprocess.CompletedProcess(argv, 0, capture["stdout"], "")
        if argv[0] == "systemctl":
            return subprocess.CompletedProcess(
                argv, 0, "Id=health.service\nActiveState=active\nSubState=running\nNRestarts=0\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "")

    def footer(age_seconds):
        stamp = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat().replace("+00:00", "Z")
        return f"-- pane live {stamp} · refresh 5s · ticks every frame --\n"

    monkeypatch.setattr(wall_view.subprocess, "run", run)
    monkeypatch.setattr(wall_view, "ci_line", lambda _home: "CI: PASS")
    monkeypatch.setattr(wall_view, "renderer_bytes_lines", lambda h: (
        ["RENDERER BYTES: GREEN pin=x renderers=12"], "GREEN"))

    capture["stdout"] = footer(4.0)
    fresh = wall_view.render(home, "health")
    assert "PANE LEASE: GREEN 1 renderers advancing" in fresh
    assert "STATE: GREEN — listed services running" in fresh

    capture["stdout"] = footer(600.0)
    frozen = wall_view.render(home, "health")
    assert "PANE LEASE: RED stale=health(" in frozen
    assert "STATE: RED — a top-pane renderer lease stopped advancing" in frozen

    capture["stdout"] = "no footer here\n"
    unknown = wall_view.render(home, "health")
    assert "PANE LEASE: UNKNOWN no-lease=health" in unknown


def test_tmux_send_failure_stays_visible_without_killing_supervisor(tmp_path, monkeypatch):
    from mishe_tauftauf import wall
    from mishe_tauftauf.tmux import TmuxError
    setup_wall(tmp_path)
    def fail(*a):
        raise TmuxError("pane disappeared during send")
    monkeypatch.setattr(wall, "tick", fail)
    assert "UNKNOWN" in seed.tick(tmp_path, "session", "genome")


def test_exhausted_send_can_be_reconciled_and_retried_without_new_wake(tmp_path, monkeypatch):
    from mishe_tauftauf import wall
    from mishe_tauftauf.tmux import TmuxError
    setup_wall(tmp_path)
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nRead wall.")
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    def fail(*a):
        raise TmuxError("lost pane")
    monkeypatch.setattr(seed, "_send", fail)
    journal = tmp_path / "checks" / f"wall-send-genome-{wake.sequence}.json"
    for attempt in range(2):
        with pytest.raises(TmuxError):
            wall.deliver(tmp_path, "session", "genome", wake.sequence, 1, "trigger")
        data = json.loads(journal.read_text())
        data["at"] = (datetime.now(timezone.utc)-timedelta(minutes=20)).isoformat()
        journal.write_text(json.dumps(data))
    assert "exhausted" in wall.deliver(tmp_path, "session", "genome", wake.sequence, 1, "trigger")
    assert "UNKNOWN" in wall.pane(tmp_path, "genome")
    wall.retry(tmp_path, "genome")
    sent = []
    monkeypatch.setattr(seed, "_send", lambda target, text: sent.append(text))
    assert wall.deliver(tmp_path, "session", "genome", wake.sequence, 1, "trigger").startswith("wake ")
    assert len(sent) == 1 and seed._state(tmp_path, "genome")[1] == wake.sequence
    assert len([e for e in Feed(tmp_path).entries() if e.body.startswith("seed wake ")]) == 1


def test_exhausted_send_recovers_after_bounded_hold_without_new_wake(tmp_path, monkeypatch):
    from mishe_tauftauf import wall
    setup_wall(tmp_path)
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nRead wall.")
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    sent = []
    monkeypatch.setattr(seed, "_send", lambda target, text: sent.append(text))
    journal = tmp_path / "checks" / f"wall-send-genome-{wake.sequence}.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(json.dumps({"at": (datetime.now(timezone.utc)-timedelta(minutes=45)).isoformat(),
                                   "attempts": 2, "phase": "delivered", "wake": wake.sequence}))
    assert wall.deliver(tmp_path, "session", "genome", wake.sequence, 1, "trigger").startswith("wake ")
    assert len(sent) == 1 and "Reconcile any prior effects" in sent[0]
    assert len([e for e in Feed(tmp_path).entries() if e.body.startswith("seed wake ")]) == 1
    data = json.loads(journal.read_text())
    assert data["attempts"] == 2 and data["phase"] == "delivered"
    # Recovery-armed exhaustion is not presented as a dead channel.
    assert "TRANSPORT: UNKNOWN" not in wall.pane(tmp_path, "genome")
    # The recovery is bounded: a fresh exhausted counter stays held, no hot loop.
    assert "exhausted" in wall.deliver(tmp_path, "session", "genome", wake.sequence, 1, "trigger")
    assert len(sent) == 1
    # A busy mind is never interrupted: hold even after the bounded interval.
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: False)
    journal.write_text(json.dumps({"at": (datetime.now(timezone.utc)-timedelta(minutes=45)).isoformat(),
                                   "attempts": 2, "phase": "sending", "wake": wake.sequence}))
    assert "mind busy" in wall.deliver(tmp_path, "session", "genome", wake.sequence, 1, "trigger")
    assert len(sent) == 1


def test_pane_keeps_shared_chat_visible_when_only_transport_is_recent(tmp_path):
    from mishe_tauftauf import wall
    setup_wall(tmp_path)
    wall.write(tmp_path, "witness", "Long edited notes\n" * 70)
    Feed(tmp_path).append("health", "The observable caller failed; investigate this shared finding.")
    for _ in range(15):
        Feed(tmp_path).append("seed", "Transport chatter; no human finding.")
    text = wall.pane(tmp_path, "witness")
    assert "CHAT.LOG" in text
    assert "The observable caller failed" in text.split("CHAT.LOG", 1)[1]
    assert "The observable caller failed" in wall.context(tmp_path, "witness")


def test_omp_attachment_requires_actual_submit_text(tmp_path, monkeypatch):
    from types import SimpleNamespace
    typed = []
    started = []
    captures = [0]
    card = "╭── 📄 #1 ───╮\n│WAKE text│\n╰ +18 lines ╯\n π > INSERT >\n╰─ 📄 #1"
    def tmux(*args, **kwargs):
        if args[0] == "send-keys" and "-l" in args:
            typed.append(args[-1])
        if args[0] == "send-keys" and args[-1] == "C-m" and typed:
            started.append(True)
        if args[0] == "capture-pane":
            captures[0] += 1
            out = " π > INSERT >" if captures[0] == 1 else card
        elif args[0] == "display-message":
            out = "omp"
        elif args[0] == "show-option":
            out = str(tmp_path)
        else:
            out = ""
        return SimpleNamespace(returncode=0, stdout=out.encode())
    monkeypatch.setattr(seed, "_tmux", tmux)
    monkeypatch.setattr(seed.time, "sleep", lambda *a: None)
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    seed._send("session:witness.1", "WAKE text")
    assert started, "Enter alone leaves the OMP attachment idle"


def test_pane_frame_leads_with_headline(tmp_path, monkeypatch):
    from mishe_tauftauf import observations
    setup_wall(tmp_path)
    (tmp_path/"checks").mkdir()
    (tmp_path/"checks"/"silence-watch.json").write_text(json.dumps(
        {"state":"ENDED","idle_seconds":5000.0,"last_action":"2026-01-01T00:00:00+00:00",
         "evidence":"chat.log 1 seed","threshold_seconds":600.0,
         "delivery":{"last_commit_seconds":63000.0,"last_activation_seconds":None}}))
    (tmp_path/"checks"/"silence-heartbeat.json").write_text(json.dumps(
        {"at":datetime.now(timezone.utc).isoformat(),"state":"ENDED"}))
    monkeypatch.setattr(observations,"run_renderer",
                        lambda *a, **k: observations.RenderedPain("genome","BODY\n",True))
    body=observations.compose_frame(tmp_path,"genome").body
    assert body.startswith("HEADLINE: RED — mind ended")
    assert "commit 17.5h ago" in body and "BODY" in body


def test_restarting_unit_stays_visible(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_view
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "services.json").write_text(json.dumps(["flaky.service"]))
    monkeypatch.setenv("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    monkeypatch.setattr(wall_view.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(
        command, 0, "Id=flaky.service\nActiveState=active\nSubState=running\nNRestarts=7\n", ""))
    monkeypatch.setattr(wall_view, "ci_line", lambda _home: "CI: PASS")
    body = wall_view.render(home, "health")
    assert "SERVICE flaky.service: GREEN active/running restarts=7" in body
    assert "0 restarts" not in body


def test_display_ages_do_not_create_observations_or_wakes(tmp_path, monkeypatch):
    from mishe_tauftauf import wall, tmux, dashboard
    setup_wall(tmp_path)
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(tmux, "_pane_stopped_or_dead", lambda *a: False)
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    monkeypatch.setattr(tmux, "capture_raw", lambda *a: f"-- pane live {stamp} · refresh 5s · ticks every frame --\n")
    sensor = ["HEADLINE: GREEN — mind ok idle 5s; watcher live; commit 0.1h ago; activation 0.5h ago\nPATCH repair: applied\nSTATE: GREEN steady\n"]
    monkeypatch.setattr(dashboard, "read", lambda *a: (sensor[0], True))
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    monkeypatch.setattr(seed, "_send", lambda *a: None)
    first = int(wall.tick(tmp_path, "session", "genome", 3600).split()[-1])
    note = tmp_path / "note"
    note.write_text("Waiting for named producer evidence.")
    seed.yield_wake(tmp_path, "genome", first, note, result="verified")
    Feed(tmp_path).append("seed", f"seed clear genome after={first}\nRotated.")
    before = len(Feed(tmp_path).entries())
    sensor[0] = sensor[0].replace("idle 5s", "idle 22s").replace("0.1h ago", "0.2h ago")
    assert wall.tick(tmp_path, "session", "genome", 3600).startswith("waiting")
    assert len(Feed(tmp_path).entries()) == before
    sensor[0] = sensor[0].replace("HEADLINE: GREEN", "HEADLINE: UNKNOWN").replace("watcher live", "watcher unknown")
    assert wall.tick(tmp_path, "session", "genome", 3600).startswith("wake")


@pytest.mark.parametrize("role", ["health", "witness", "genome"])
def test_patch_and_headline_failures_are_meaningful_for_every_monitor(role):
    from mishe_tauftauf import wall
    healthy = "HEADLINE: GREEN — mind ok idle 5s; watcher live; commit unknown; activation unknown\nPATCH repair: applied\nSERVICES: GREEN — 1 listed\nSTATE: GREEN\n"
    assert wall.observation_text(role, healthy) != wall.observation_text(role, healthy.replace("PATCH repair: applied", "PATCH repair: revert-failed"))
    assert wall.observation_text(role, healthy) != wall.observation_text(role, healthy.replace("watcher live", "watcher unknown"))


def test_witness_digest_normalizes_only_volatile_evidence_and_chat_text():
    from mishe_tauftauf.wall import observation_text
    first = (
        "CHAT RATE: RED — source: 8 entries. Trace it.\n"
        "PUBLICATION RESULT: CLEAR semantic=clear\n"
        "Active evidence: private report=/site/private/a.json\n"
        "Evidence: private report=/site/private/b.json\n"
        "Evidence: /site/private/c.json\n"
        "ANOMALY: RED id=stable transition=opened\n"
        "LATEST CHAT.LOG TEXT (all roles):\n"
        "42 genome: first changing message\n"
        "GOAL: preserve monitoring\n"
        "STATE: RED\n"
    )
    second = first.replace("/site/private/a.json", "/other/private/a.json")
    second = second.replace("/site/private/b.json", "/other/private/b.json")
    second = second.replace("/site/private/c.json", "/other/private/c.json")
    second = second.replace("8 entries. Trace it.", "11 entries. More churn.")
    second = second.replace("first changing message", "later changing message")
    assert observation_text("witness", first) == observation_text("witness", second)
    assert observation_text("witness", first) != observation_text("witness", first.replace("CHAT RATE: RED", "CHAT RATE: GREEN"))
    assert observation_text("witness", first) != observation_text("witness", first.replace("id=stable", "id=changed"))
    assert observation_text("witness", first) != observation_text("witness", first.replace("STATE: RED", "STATE: GREEN"))
    framed = ("HEADLINE: GREEN — mind ok; watcher live\n"
              "HEADLINE: GREEN — mind ok idle 5s; watcher live; commit 0.1h ago; activation 3.4h ago\n") + first
    assert observation_text("witness", framed) == observation_text(
        "witness", framed.replace("idle 5s", "idle 61s").replace("0.1h ago", "0.9h ago"))


def test_witness_digest_excludes_advisor_status_lines():
    from mishe_tauftauf.wall import observation_text
    frame = (
        "HEADLINE: GREEN — mind ok\n"
        "ANALYSIS ADVISOR: READY wake=1 suggested=investigate seconds=2 artifact=/tmp/1.json\n"
        "STATE: GREEN\n"
    )
    changed_advice = frame.replace(
        "READY wake=1 suggested=investigate seconds=2 artifact=/tmp/1.json",
        "UNKNOWN wake=2 suggested=wait seconds=9 artifact=/tmp/2.json",
    )
    assert observation_text("witness", frame) == observation_text("witness", changed_advice)
    assert observation_text("witness", frame) != observation_text(
        "witness", frame.replace("STATE: GREEN", "STATE: RED"))


def test_wall_outcome_requires_evidence_and_cli_records_prose(tmp_path):
    from mishe_tauftauf import wall
    from mishe_tauftauf.cli import main
    from mishe_tauftauf.records import payload
    setup_wall(tmp_path)
    note = tmp_path / "note.md"
    note.write_text("Removed the failed camera hypothesis after a controlled comparison.")
    evidence = tmp_path / "artifacts" / "comparison.txt"
    evidence.parent.mkdir()
    evidence.write_text("control=4 candidate=4")
    with pytest.raises(ValueError, match="evidence"):
        wall.outcome(tmp_path, "discover", "hypothesis-changed", note.read_text(), tmp_path / "absent")
    assert main(["--home", str(tmp_path), "wall", "outcome", "--owner", "discover", "--kind", "hypothesis-changed", "--file", str(note), "--evidence", str(evidence)]) == 0
    entry = Feed(tmp_path).entries()[-1]
    assert entry.body.startswith("Wall outcome hypothesis-changed")
    assert payload(entry)["evidence"]["sha256"]

def test_wall_outcome_refuses_already_bound_evidence_path(tmp_path):
    """D-check: outcome refuses an evidence path already bound by another outcome."""
    from mishe_tauftauf import wall
    setup_wall(tmp_path)
    evidence = tmp_path / "artifacts" / "sample.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("control=4 candidate=4")
    # First outcome binds the evidence path
    wall.outcome(tmp_path, "discover", "hypothesis-changed", "First outcome.", evidence)
    # Second outcome with the same evidence path must be refused
    with pytest.raises(ValueError, match="already bound"):
        wall.outcome(tmp_path, "health", "accepted", "Second outcome.", evidence)


def test_wall_outcome_publishes_the_supersession_link(tmp_path):
    """The writer names the record digest an outcome re-evidences.

    The reading side decodes a supersession link from the record payload, but
    only a publication that actually carries one can produce it. This pins the
    writing half: the link is in the structured payload, the reader sees it,
    and it is not prose.
    """
    from mishe_tauftauf import wall
    from mishe_tauftauf.records import payload
    from mishe_tauftauf.outcome_events import classify_outcomes
    setup_wall(tmp_path)
    evidence = tmp_path / "artifacts" / "recovered.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("recovered bytes")
    earlier = hashlib.sha256(b"earlier record").hexdigest()
    wall.outcome(tmp_path, "discover", "hypothesis-changed", "Re-evidenced row.",
                 evidence, supersedes=[earlier])
    entry = Feed(tmp_path).entries()[-1]
    assert payload(entry)["supersedes"] == [earlier]
    events = classify_outcomes(tmp_path, datetime(2026, 1, 1, tzinfo=timezone.utc))["events"]
    assert [event["supersedes"] for event in events] == [[earlier]]


def test_wall_outcome_refuses_a_malformed_supersession_digest(tmp_path):
    """A link that names nothing is refused, not published as a claim."""
    from mishe_tauftauf import wall
    setup_wall(tmp_path)
    evidence = tmp_path / "artifacts" / "recovered.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("recovered bytes")
    with pytest.raises(ValueError, match="supersedes"):
        wall.outcome(tmp_path, "discover", "hypothesis-changed", "Bad link.", evidence,
                     supersedes=["not-a-digest"])
    assert not [entry for entry in Feed(tmp_path).entries()
                if entry.body.startswith("Wall outcome ")]


def test_wall_outcome_without_a_supersession_carries_no_link(tmp_path):
    """A publication that supersedes nothing claims nothing."""
    from mishe_tauftauf import wall
    from mishe_tauftauf.records import payload
    setup_wall(tmp_path)
    evidence = tmp_path / "artifacts" / "plain.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("plain bytes")
    wall.outcome(tmp_path, "discover", "accepted", "Plain outcome.", evidence)
    assert "supersedes" not in payload(Feed(tmp_path).entries()[-1])


def test_wall_outcome_cli_writes_the_supersession_link(tmp_path):
    """The flag reaches the record; the link is not a prose convention."""
    from mishe_tauftauf.cli import main
    from mishe_tauftauf.records import payload
    setup_wall(tmp_path)
    note = tmp_path / "note.md"
    note.write_text("Re-evidenced the amended row with a copy of its current bytes.")
    evidence = tmp_path / "artifacts" / "recovered.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("recovered bytes")
    earlier = hashlib.sha256(b"earlier record").hexdigest()
    assert main(["--home", str(tmp_path), "wall", "outcome", "--owner", "discover",
                 "--kind", "hypothesis-changed", "--file", str(note),
                 "--evidence", str(evidence), "--supersedes", earlier]) == 0
    assert payload(Feed(tmp_path).entries()[-1])["supersedes"] == [earlier]


def test_wall_outcome_concurrent_duplicate_is_refused(tmp_path):
    """Two processes cannot bind one evidence path in concurrent publications."""
    setup_wall(tmp_path)
    evidence = tmp_path / "artifacts" / "sample.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("shared evidence")
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    results = context.Queue()
    workers = [
        context.Process(target=_concurrent_wall_outcome,
                        args=(tmp_path, evidence, role, barrier, results))
        for role in ("discover", "health")
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)
    assert all(not worker.is_alive() for worker in workers)
    assert [worker.exitcode for worker in workers] == [0, 0]
    outcomes = [results.get(timeout=2) for _ in workers]
    assert sorted(result[0] for result in outcomes) == ["published", "refused"]
    assert sum("already bound" in result[1] for result in outcomes if result[0] == "refused") == 1
    assert len([entry for entry in Feed(tmp_path).entries()
                if entry.body.startswith("Wall outcome ")]) == 1


def test_wall_outcome_allows_distinct_evidence_paths(tmp_path):
    """D-check: outcome allows distinct evidence paths."""
    from mishe_tauftauf import wall
    setup_wall(tmp_path)
    evidence1 = tmp_path / "artifacts" / "sample1.md"
    evidence1.parent.mkdir(parents=True)
    evidence1.write_text("control=4 candidate=4")
    evidence2 = tmp_path / "artifacts" / "sample2.md"
    evidence2.write_text("control=5 candidate=5")
    wall.outcome(tmp_path, "discover", "hypothesis-changed", "First outcome.", evidence1)
    wall.outcome(tmp_path, "health", "accepted", "Second outcome.", evidence2)

def test_wall_outcome_makes_only_newly_bound_evidence_read_only(tmp_path):
    from mishe_tauftauf import wall
    setup_wall(tmp_path)
    evidence = tmp_path / "artifacts" / "sample.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("final evidence\n")
    wall.outcome(tmp_path, "genome", "accepted", "Recorded evidence.", evidence)
    assert evidence.read_bytes() == b"final evidence\n"
    assert evidence.stat().st_mode & 0o222 == 0
    with pytest.raises(ValueError, match="already bound"):
        wall.outcome(tmp_path, "health", "accepted", "Replay refused.", evidence)


def test_wall_outcome_restores_evidence_mode_when_append_fails(tmp_path, monkeypatch):
    from mishe_tauftauf import wall
    setup_wall(tmp_path)
    evidence = tmp_path / "artifacts" / "sample.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("final evidence\n")
    original_mode = evidence.stat().st_mode & 0o7777

    def fail_append(*args, **kwargs):
        raise ValueError("simulated append refusal")

    monkeypatch.setattr(Feed, "append_record", fail_append)
    with pytest.raises(ValueError, match="simulated append refusal"):
        wall.outcome(tmp_path, "genome", "accepted", "Not published.", evidence)

    assert evidence.stat().st_mode & 0o7777 == original_mode
    assert not any(entry.body.startswith("Wall outcome ")
                   for entry in Feed(tmp_path).entries())


def test_wall_outcome_ignores_an_outcome_recorded_before_the_clause_time(tmp_path):
    """D-check scope: a pre-clause outcome does not bind its evidence path.

    The write-once rule applies from `EVIDENCE_BINDING_CLAUSE_TIME` onward. A
    rule introduced after evidence was already lawfully bound cannot refuse that
    evidence retroactively, so an outcome recorded before the clause leaves the
    path free for a later outcome.
    """
    from mishe_tauftauf import wall
    from mishe_tauftauf.discovery import EVIDENCE_BINDING_CLAUSE_TIME
    setup_wall(tmp_path)
    evidence = tmp_path / "artifacts" / "sample.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("control=4 candidate=4")
    wall.outcome(tmp_path, "discover", "hypothesis-changed", "Old outcome.", evidence)
    old = Feed(tmp_path).entries()[-1]
    # Backdate the citing entry to before the clause: a rule introduced after
    # evidence was lawfully bound cannot refuse that evidence retroactively.
    # The timestamp lives in the entry header, not on the content-addressed
    # record, so the record bytes stay valid evidence.
    before = (EVIDENCE_BINDING_CLAUSE_TIME
              - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    _backdate_entry(tmp_path, old.sequence, before)
    assert datetime.fromisoformat(
        Feed(tmp_path).entries()[-1].timestamp.removesuffix("Z") + "+00:00"
    ) < EVIDENCE_BINDING_CLAUSE_TIME
    # A pre-clause binding must not refuse a later outcome's evidence
    wall.outcome(tmp_path, "health", "accepted", "New outcome.", evidence)
    assert [entry for entry in Feed(tmp_path).entries()
            if entry.body.startswith("Wall outcome accepted")]



def test_wall_outcome_counts_post_clause_replay_of_pre_clause_record(tmp_path):
    from mishe_tauftauf import wall, records
    from mishe_tauftauf.discovery import EVIDENCE_BINDING_CLAUSE_TIME
    setup_wall(tmp_path)
    evidence = tmp_path / "artifacts" / "sample.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("control=4 candidate=4")
    wall.outcome(tmp_path, "discover", "hypothesis-changed", "Old outcome.", evidence)
    old = Feed(tmp_path).entries()[-1]
    before = (EVIDENCE_BINDING_CLAUSE_TIME
              - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    _backdate_entry(tmp_path, old.sequence, before)
    Feed(tmp_path).append_record(old.source, "\n".join(old.body.splitlines()[:-1]),
                                 records.payload(old), kind="wall-outcome")
    with pytest.raises(ValueError, match="already bound"):
        wall.outcome(tmp_path, "health", "accepted", "Replay refused.", evidence)


def test_wall_outcome_does_not_bind_an_inline_historical_citation(tmp_path):
    from mishe_tauftauf import wall
    from mishe_tauftauf.discovery import EVIDENCE_BINDING_CLAUSE_TIME
    setup_wall(tmp_path)
    evidence = tmp_path / "artifacts" / "sample.md"
    evidence.parent.mkdir()
    evidence.write_text("immutable evidence\n")
    wall.outcome(tmp_path, "discover", "accepted", "Old contribution.", evidence)
    old = Feed(tmp_path).entries()[-1]
    before = (EVIDENCE_BINDING_CLAUSE_TIME - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    _backdate_entry(tmp_path, old.sequence, before)
    Feed(tmp_path).append("health", "Historical citation: " + old.body.splitlines()[-1])
    wall.outcome(tmp_path, "health", "accepted", "Actual new contribution.", evidence)
    with pytest.raises(ValueError, match="already bound"):
        wall.outcome(tmp_path, "senses", "accepted", "Another contribution.", evidence)

def test_delivery_dashboard_separates_source_and_runtime_and_marks_incomplete(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_view
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health/services.json").write_text(json.dumps(["test.service"]))
    (home / "patches").mkdir()
    (home / "patches/change.json").write_text(json.dumps({"phase": "applied"}))
    monkeypatch.setattr(wall_view, "ci_line", lambda *a: "CI: PASS sha=older")
    def commands(command, **kwargs):
        output = ("newer\n" if "rev-parse" in command else " M file\n" if "status" in command else
                  "Id=test.service\nActiveState=active\nSubState=running\nNRestarts=0\n")
        return subprocess.CompletedProcess(command, 0, output, "")
    monkeypatch.setattr(wall_view.subprocess, "run", commands)
    text = wall_view.render(home, "genome")
    assert "CI: PASS sha=older" in text and "SOURCE: checkout=newer changed_paths=1" in text
    assert "DEPLOYED: root=" in text and "sha256=" in text
    assert "PATCH change: applied delivery=incomplete" in text
    (home / "patches/change.json").write_text(json.dumps({"phase": "applied", "delivery_verified": True}))
    assert "PATCH change: applied delivery=incomplete" in wall_view.render(home, "genome")
    (home / "patches/change.json").write_text(json.dumps({
        "phase": "applied", "delivery_verified": True, "verification": {"at": "2026-10-01T00:00:00+00:00"}}))
    assert "PATCH change: applied delivery=verified" in wall_view.render(home, "genome")


def test_dashboard_flags_runtime_drift_against_the_pin(tmp_path, monkeypatch):
    from mishe_tauftauf import runtime_source, wall_view
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    running = tmp_path / "running"
    assert wall_view._runtime_state(home, running) == ("UNPINNED", "RUNTIME: pin=UNPINNED")
    (home / "health/runtime-release.json").write_text("{}")
    pinned = tmp_path / "release"

    def at(source):
        return lambda h, default: source

    monkeypatch.setattr(runtime_source, "source_for", at(pinned))
    state, line = wall_view._runtime_state(home, running, {"unit.service": [str(pinned)]})
    assert state == "MATCH" and line.endswith("MATCH")
    state, line = wall_view._runtime_state(home, running, {"unit.service": [str(tmp_path / "other")]})
    assert state == "DRIFT" and "unit.service" in line and str(pinned) in line
    # The release coordinator is declared on the development checkout, not the pin.
    state, line = wall_view._runtime_state(
        home, running, {"session-coordination.service": [str(tmp_path)]})
    assert state == "DECLARED" and line.endswith("observes the checkout")
    # Any other root for that unit is still a drift, not a declared exception.
    state, line = wall_view._runtime_state(
        home, running, {"session-coordination.service": [str(tmp_path / "other")]})
    assert state == "DRIFT"
    assert wall_view._runtime_state(home, running)[1].endswith("services=UNKNOWN")
    assert wall_view._runtime_state(home, running, {})[1].endswith("services=none")

    def broken(h, default):
        raise ValueError("runtime pin invalid: missing release")

    monkeypatch.setattr(runtime_source, "source_for", broken)
    assert "pin=UNKNOWN" in wall_view._runtime_state(home, running, [])[1]


def test_import_roots_reads_pythonpath_from_systemd_environment():
    from mishe_tauftauf import wall_view
    assert wall_view._import_roots("PYTHONPATH=/srv/checkout/src") == ["/srv/checkout"]
    assert wall_view._import_roots(
        "PYTHONPATH=/a/releases/x:/a/releases/x/src XDG_RUNTIME_DIR=/run/user/1000") == ["/a/releases/x", "/a/releases/x"]
    assert wall_view._import_roots("HOME=/root") == []


def test_dashboard_runtime_drift_downgrades_the_state_line(tmp_path, monkeypatch):
    from mishe_tauftauf import runtime_source, wall_view
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health/services.json").write_text(json.dumps(["test.service"]))
    (home / "health/runtime-release.json").write_text("{}")
    monkeypatch.setattr(wall_view, "ci_line", lambda *a: "CI: PASS")
    other = tmp_path / "other"

    def commands(command, **kwargs):
        output = ("pinned\n" if "rev-parse" in command else "" if "status" in command else
                  "Id=test.service\nActiveState=active\nSubState=running\nNRestarts=0\n"
                  f"Environment=PYTHONPATH={other}/src\n")
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(wall_view.subprocess, "run", commands)
    monkeypatch.setattr(runtime_source, "source_for", lambda h, default: other)
    matched = wall_view.render(home, "genome")
    assert f"RUNTIME: pin={other} services={other} MATCH" in matched
    assert "STATE: GREEN — listed services running" in matched

    monkeypatch.setattr(runtime_source, "source_for", lambda h, default: tmp_path / "pinned")
    drifted = wall_view.render(home, "genome")
    assert "RUNTIME: pin=" in drifted and f"services={other} DRIFT" in drifted
    assert "STATE: RED — runtime drift: service import roots differ from the pinned release" in drifted
    assert "STATE: GREEN" not in drifted


def test_renderer_bytes_names_a_superseded_release(tmp_path, monkeypatch):
    """The measured ledger drift: pin 1543b3a, live d7bf4e4, every reading GREEN."""
    from mishe_tauftauf import runtime_source, wall_view
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "top-pains").mkdir()
    (home / "health/windows.json").write_text(json.dumps(["ledger", "tail"]))
    (home / "top-pains/ledger").write_text("renderer")
    pinned_path = (tmp_path / "releases/pin").resolve()
    pinned = str(pinned_path)
    old = str((tmp_path / "releases/old").resolve())
    monkeypatch.setenv("MISHE_SEED_SESSION", "owned")
    monkeypatch.setattr(runtime_source, "source_for", lambda h, default: pinned_path)
    monkeypatch.setattr(wall_view, "_renderer_pids", lambda session: {"ledger": 4242})
    monkeypatch.setattr(wall_view, "_live_import_roots", lambda pid: [pinned])
    lines, state = wall_view.renderer_bytes_lines(home)
    assert state == "GREEN" and "renderers=1" in lines[0]
    # ``tail`` carries no top-pain, so it is not a renderer and not checked.
    monkeypatch.setattr(wall_view, "_live_import_roots", lambda pid: [old])
    lines, state = wall_view.renderer_bytes_lines(home)
    assert state == "DRIFT" and "ledger" in lines[0] and old in lines[0]
    # A renderer whose process cannot be read is UNKNOWN, never silently GREEN.
    monkeypatch.setattr(wall_view, "_renderer_pids", lambda session: {})
    lines, state = wall_view.renderer_bytes_lines(home)
    assert state == "UNKNOWN" and "ledger" in lines[0]
    monkeypatch.setattr(wall_view, "_renderer_pids", lambda session: {"ledger": 4242})
    monkeypatch.setattr(wall_view, "_live_import_roots", lambda pid: None)
    lines, state = wall_view.renderer_bytes_lines(home)
    assert state == "UNKNOWN" and "ledger" in lines[0]


def test_live_import_roots_keeps_unreadable_distinct_from_no_pythonpath(monkeypatch, tmp_path):
    """``proc_table`` decodes; only the read/unreadable split is wall_view's."""
    import pathlib
    from mishe_tauftauf import wall_view
    environ = tmp_path / "environ"
    environ.write_bytes(b"HOME=/root\0PYTHONPATH=/site/releases/abc/src\0")
    absent = tmp_path / "absent"
    monkeypatch.setattr(wall_view, "Path",
                        lambda *a: environ if a[0] == "/proc" else pathlib.Path(*a))
    assert wall_view._live_import_roots(1) == ["/site/releases/abc"]
    # A readable process that names no PYTHONPATH is empty, not unreadable.
    environ.write_bytes(b"HOME=/root\0")
    assert wall_view._live_import_roots(1) == []
    monkeypatch.setattr(wall_view, "Path",
                        lambda *a: absent if a[0] == "/proc" else pathlib.Path(*a))
    assert wall_view._live_import_roots(1) is None


def test_health_state_folds_renderer_byte_drift(tmp_path, monkeypatch):
    from mishe_tauftauf import wall_view
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    monkeypatch.setattr(wall_view, "ci_line", lambda *a: "CI: PASS")
    monkeypatch.setattr(wall_view, "_service_block", lambda h: (
        [], {}, "GREEN — listed services running; CI reported separately"))
    monkeypatch.setattr(wall_view, "_runtime_state", lambda *a, **k: ("MATCH", "RUNTIME: pin=x MATCH"))
    monkeypatch.setattr(wall_view, "pane_lease_lines", lambda h: (
        ["PANE LEASE: GREEN 12 renderers advancing"], "GREEN"))
    monkeypatch.setattr(wall_view, "renderer_bytes_lines", lambda h: (
        ["RENDERER BYTES: RED pin=x stale=ledger(live=y)"], "DRIFT"))
    text = wall_view.render(home, "health")
    assert "RENDERER BYTES: RED pin=x stale=ledger(live=y)" in text
    assert "STATE: RED — a top-pane renderer imports a superseded release" in text
    monkeypatch.setattr(wall_view, "renderer_bytes_lines", lambda h: (
        ["RENDERER BYTES: GREEN pin=x renderers=12"], "GREEN"))
    assert "STATE: GREEN — listed services running" in wall_view.render(home, "health")


def test_dashboard_runtime_drift_names_undeclared_units(tmp_path, monkeypatch):
    from mishe_tauftauf import runtime_source, wall_view
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health/services.json").write_text(json.dumps(["alpha.service", "beta.service", "gamma.service"]))
    (home / "health/runtime-release.json").write_text("{}")
    monkeypatch.setattr(wall_view, "ci_line", lambda *a: "CI: PASS")
    pinned = tmp_path / "pinned"

    def commands(command, **kwargs):
        output = ("pinned\n" if "rev-parse" in command else "" if "status" in command else
                  "Id=alpha.service\nActiveState=active\nSubState=running\nNRestarts=0\n"
                  f"Environment=PYTHONPATH={pinned}/src\n"
                  "Id=beta.service\nActiveState=active\nSubState=running\nNRestarts=0\n"
                  f"Environment=PYTHONPATH={pinned}/src\n"
                  "Id=gamma.service\nActiveState=active\nSubState=running\nNRestarts=0\n"
                  f"Environment=PYTHONPATH={tmp_path}/other/src\n")
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(wall_view.subprocess, "run", commands)
    monkeypatch.setattr(runtime_source, "source_for", lambda h, default: pinned)
    text = wall_view.render(home, "genome")
    assert "gamma.service" in text
    assert "alpha.service" not in text.split("DRIFT")[0].split("services=")[1]
    assert "DRIFT — undeclared: gamma.service" in text

def test_dashboard_declares_the_coordination_checkout_exception(tmp_path, monkeypatch):
    from mishe_tauftauf import runtime_source, wall_view
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health/services.json").write_text(json.dumps(["session-coordination.service"]))
    (home / "health/runtime-release.json").write_text("{}")
    monkeypatch.setattr(wall_view, "ci_line", lambda *a: "CI: PASS")
    monkeypatch.setattr(runtime_source, "source_for", lambda h, default: tmp_path / "release")

    def commands(command, **kwargs):
        output = ("pinned\n" if "rev-parse" in command else "" if "status" in command else
                  "Id=session-coordination.service\nActiveState=active\nSubState=running\nNRestarts=0\n"
                  f"Environment=PYTHONPATH={tmp_path}/src\n")
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(wall_view.subprocess, "run", commands)
    text = wall_view.render(home, "genome")
    assert f"services={tmp_path} DECLARED" in text
    assert "STATE: GREEN — listed services running" in text
    assert "STATE: RED" not in text


def test_dashboard_declares_a_recorded_off_pin_root(tmp_path, monkeypatch):
    """A site record names an off-pin snapshot; the pin need not carry the module."""
    from mishe_tauftauf import runtime_source, wall_view
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health/services.json").write_text(json.dumps(["observer.service", "pinned.service"]))
    (home / "health/runtime-release.json").write_text("{}")
    bundle = tmp_path / "bundle"
    (home / "health/declared-roots.json").write_text(json.dumps({
        "version": 1,
        "units": {"observer.service": {
            "root": str(bundle / "src"), "owner": "genome",
            "provenance": "bundle/activation.json",
            "reason": "the pin does not carry the module the unit runs",
            "retire_when": "a pin release carrying the module"}}}))
    monkeypatch.setattr(wall_view, "ci_line", lambda *a: "CI: PASS")
    pinned = tmp_path / "pinned"

    def commands(command, **kwargs):
        blocks = [
            "Id=observer.service\nActiveState=active\nSubState=running\nNRestarts=0\n"
            f"Environment=PYTHONPATH={bundle}/src",
            "Id=pinned.service\nActiveState=active\nSubState=running\nNRestarts=0\n"
            f"Environment=PYTHONPATH={pinned}/src",
        ]
        output = ("pinned\n" if "rev-parse" in command else "" if "status" in command
                  else "\n\n".join(blocks))
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(wall_view.subprocess, "run", commands)
    monkeypatch.setattr(runtime_source, "source_for", lambda h, default: pinned)
    text = wall_view.render(home, "genome")
    assert f"observer.service={bundle} observes the checkout" in text
    assert "STATE: GREEN — listed services running" in text
    assert "DRIFT" not in text


def test_dashboard_treats_a_broken_declaration_record_as_no_declaration(tmp_path, monkeypatch):
    """A malformed allowlist declares nothing: the unit stays drift, not green."""
    from mishe_tauftauf import runtime_source, wall_view
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health/services.json").write_text(json.dumps(["observer.service"]))
    (home / "health/runtime-release.json").write_text("{}")
    (home / "health/declared-roots.json").write_text("{not json")
    monkeypatch.setattr(wall_view, "ci_line", lambda *a: "CI: PASS")
    pinned = tmp_path / "pinned"
    bundle = tmp_path / "bundle"

    def commands(command, **kwargs):
        output = ("pinned\n" if "rev-parse" in command else "" if "status" in command else
                  "Id=observer.service\nActiveState=active\nSubState=running\nNRestarts=0\n"
                  f"Environment=PYTHONPATH={bundle}/src\n")
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(wall_view.subprocess, "run", commands)
    monkeypatch.setattr(runtime_source, "source_for", lambda h, default: pinned)
    assert "DRIFT — undeclared: observer.service" in wall_view.render(home, "genome")


def test_declared_root_entries_collapse_a_src_suffix(tmp_path):
    from mishe_tauftauf import runtime_source
    (tmp_path / "health").mkdir()
    (tmp_path / "health/declared-roots.json").write_text(json.dumps(
        {"units": {"a.service": {"root": "/srv/bundle/src"},
                   "b.service": {"root": "/srv/other"}}}))
    assert runtime_source.declared_root_entries(tmp_path) == {"a.service": "/srv/bundle",
                                                             "b.service": "/srv/other"}
    assert runtime_source.declared_checkout_root(tmp_path, "a.service") == pathlib.Path("/srv/bundle")
    assert runtime_source.declared_checkout_root(tmp_path, "b.service") == pathlib.Path("/srv/other")
    assert runtime_source.declared_checkout_root(tmp_path, "unknown.service") is None
    (tmp_path / "health/declared-roots.json").write_text(json.dumps({"units": []}))
    assert runtime_source.declared_root_entries(tmp_path) == {}


def test_runtime_drift_state_forces_notification_past_a_hold_filter(tmp_path, monkeypatch):
    from mishe_tauftauf import wall, tmux, dashboard
    from mishe_tauftauf.records import payload
    setup_wall(tmp_path)
    monkeypatch.setattr(tmux, "owns_session", lambda *a: True)
    monkeypatch.setattr(tmux, "_pane_stopped_or_dead", lambda *a: False)
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    monkeypatch.setattr(tmux, "capture_raw", lambda *a: f"-- pane live {stamp} · refresh 5s · ticks every frame --\n")
    monkeypatch.setattr(seed, "_mind_ready", lambda *a: True)
    monkeypatch.setattr(seed, "_send", lambda *a: None)
    monkeypatch.setattr(observations, "run_filter", lambda *a, **k: observations.FilterResult(False, "hold"))
    sensor = ["HEADLINE: GREEN — mind ok idle 5s; watcher live; commit unknown; activation unknown\n"
              "STATE: GREEN — listed services running; CI reported separately\n"]
    monkeypatch.setattr(dashboard, "read", lambda *a: (sensor[0], True))

    def last_notify():
        record = [e for e in Feed(tmp_path).entries() if "[record] " in e.body][-1]
        return payload(record)["notify"]

    first = int(wall.tick(tmp_path, "session", "genome", 3600).split()[-1])
    assert last_notify() is False
    note = tmp_path / "note"
    note.write_text("Held for the drift observation.")
    seed.yield_wake(tmp_path, "genome", first, note, result="verified")
    Feed(tmp_path).append("seed", f"seed clear genome after={first}\nRotated.")
    sensor[0] = sensor[0].replace(
        "STATE: GREEN — listed services running; CI reported separately",
        "STATE: RED — runtime drift: service import roots differ from the pinned release")
    assert wall.tick(tmp_path, "session", "genome", 3600).startswith("wake")
    assert last_notify() is True


@pytest.mark.parametrize("value", [[1800], {"genome": -1}, {"genome": float("inf")}, {"genome": "soon"}])
def test_invalid_periodic_review_config_is_visible_unknown(tmp_path, value):
    setup_wall(tmp_path)
    config = json.loads((tmp_path / "coordination-mode.json").read_text())
    config["self_pick_seconds"] = value
    (tmp_path / "coordination-mode.json").write_text(json.dumps(config))
    assert "UNKNOWN" in seed.tick(tmp_path, "session", "genome")

def test_seed_yield_race_restage_mid_settle_preserves_newer_anchor(tmp_path, monkeypatch, capsys):
    """If the staging anchor is restaged mid-settle, the newer bytes are preserved.

    Mirrors the dm consumption race guard (test_wall_dm_race_restage_mid_send...):
    the anchor stat must be captured before the wall write so a restage during
    settle is detected; otherwise the guard reads the anchor twice after the fact
    and unconditionally destroys whatever is on disk.
    """
    setup_wall(tmp_path)
    from mishe_tauftauf import cli, wall
    (tmp_path / "walls").mkdir()
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nRead your wall.")
    wall_path = tmp_path / "walls" / "genome.md"
    wall_path.write_text("old plate\n", encoding="utf-8")
    anchor = tmp_path / "walls" / "genome-NOTES"
    anchor.write_text("original staged plate\n", encoding="utf-8")
    os.utime(wall_path, (1_700_000_000, 1_700_000_000))
    os.utime(anchor, (1_700_000_100, 1_700_000_100))

    original_write = wall.write
    def restaging_write(*args, **kwargs):
        anchor.write_text("restaged during settle", encoding="utf-8")
        return original_write(*args, **kwargs)
    monkeypatch.setattr(wall, "write", restaging_write)

    code = cli.main(["--home", str(tmp_path), "seed", "yield", "--slug", "genome",
                     "--wake", str(wake.sequence), "--file", "NOTES", "--result", "changed"])
    assert code == 0, "settle succeeds even when anchor is restaged mid-flight"
    assert anchor.exists(), "restaged anchor must survive — newer bytes preserved"
    assert anchor.read_text(encoding="utf-8") == "restaged during settle"
    captured = capsys.readouterr()
    assert "not consumed" in captured.out
def test_wall_outcome_enforces_eligible_immutable_path_allowlist(tmp_path):
    from mishe_tauftauf import wall

    setup_wall(tmp_path)
    allowed = [
        (tmp_path / "artifacts" / "result.md", "artifacts"),
        (tmp_path / "discovery" / "scan-test.json", "discovery"),
        (tmp_path / "dms" / "DM-test.md", "dms"),
    ]
    for evidence, directory in allowed:
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(f"immutable {directory} evidence\n")
        wall.outcome(tmp_path, "genome", "accepted", f"Allowed {directory}.", evidence)

    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text("outside\n")
    rejected = [
        tmp_path / "walls" / "genome.md",
        tmp_path / "notes" / "progress.md",
        tmp_path / "tmp" / "scratch.md",
        tmp_path / "health" / "latest.json",
        tmp_path / "research" / "report.md",
        tmp_path / "ci" / "latest.json",
        tmp_path / "chat.log",
        outside,
    ]
    for evidence in rejected:
        evidence.parent.mkdir(parents=True, exist_ok=True)
        if not evidence.exists():
            evidence.write_text("mutable or out-of-scope\n")
        with pytest.raises(ValueError, match="eligible single file"):
            wall.outcome(tmp_path, "genome", "accepted", "Must refuse.", evidence)

    artifacts = tmp_path / "artifacts"
    escape = artifacts / ".." / "walls" / "genome.md"
    with pytest.raises(ValueError, match="eligible single file"):
        wall.outcome(tmp_path, "genome", "accepted", "Traversal must refuse.", escape)

    target = tmp_path / "artifacts" / "target.md"
    target.write_text("target\n")
    alias = tmp_path / "artifacts" / "alias.md"
    alias.symlink_to(target)
    with pytest.raises(ValueError, match="eligible single file"):
        wall.outcome(tmp_path, "genome", "accepted", "Symlink must refuse.", alias)

    assert len([entry for entry in Feed(tmp_path).entries()
                if entry.body.startswith("Wall outcome ")]) == len(allowed)


def test_wall_outcome_refuses_already_bound_allowed_scan_path(tmp_path):
    from mishe_tauftauf import wall

    setup_wall(tmp_path)
    evidence = tmp_path / "discovery" / "scan-once.json"
    evidence.parent.mkdir()
    evidence.write_text('{"scan": 1}\n')
    wall.outcome(tmp_path, "genome", "accepted", "First scan outcome.", evidence)
    with pytest.raises(ValueError, match="already bound"):
        wall.outcome(tmp_path, "genome", "accepted", "Duplicate scan outcome.", evidence)
    assert len([entry for entry in Feed(tmp_path).entries()
                if entry.body.startswith("Wall outcome ")]) == 1
def test_wall_outcome_fails_closed_on_corrupt_history(tmp_path):
    from mishe_tauftauf import discovery, wall

    setup_wall(tmp_path)
    evidence = tmp_path / "artifacts" / "fresh.md"
    evidence.parent.mkdir()
    evidence.write_text("new immutable evidence\n")
    tape = tmp_path / "chat.log"
    tape.write_bytes(b"corrupt historical tape\n")
    before = tape.read_bytes()

    with pytest.raises(ValueError, match="cannot derive bound evidence paths"):
        wall.outcome(tmp_path, "genome", "accepted", "Must fail closed.", evidence)

    assert tape.read_bytes() == before
    assert discovery._ledger_evidence_binding(tmp_path)["state"] == "unknown"
def test_wall_outcome_hashes_evidence_after_waiting_for_publication_lock(tmp_path, monkeypatch):
    from mishe_tauftauf import wall
    from mishe_tauftauf.records import payload

    setup_wall(tmp_path)
    evidence = tmp_path / "artifacts" / "changing.md"
    evidence.parent.mkdir()
    evidence.write_text("bytes before lock\n")
    observed = b"bytes after lock wait\n"

    def change_while_locked(home):
        evidence.write_bytes(observed)
        return set()

    monkeypatch.setattr(wall, "_bound_evidence_paths", change_while_locked)
    wall.outcome(tmp_path, "genome", "accepted", "Bound after lock wait.", evidence)
    entry = next(entry for entry in Feed(tmp_path).entries()
                 if entry.body.startswith("Wall outcome "))
    assert payload(entry)["evidence"]["sha256"] == hashlib.sha256(observed).hexdigest()


def test_health_dashboard_states_an_unreadable_renderer_only_on_evidence(tmp_path, monkeypatch):
    """An empty renderer-bytes result means there was nothing to inspect.

    It is not an unreadable renderer, so the health state must not claim one;
    a renderer-bytes line that names an unreadable renderer must.
    """
    from mishe_tauftauf import wall_view

    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "services.json").write_text(json.dumps(["health.service"]))
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)

    def systemctl_ok(command, **_kwargs):
        return subprocess.CompletedProcess(
            command, 0,
            "Id=health.service\nActiveState=active\nSubState=running\nNRestarts=0\n",
            "")

    monkeypatch.setattr(wall_view.subprocess, "run", systemctl_ok)
    monkeypatch.setattr(wall_view, "ci_line", lambda _home: "CI: PASS")
    monkeypatch.setattr(wall_view, "pane_lease_lines", lambda _home: ([], "GREEN"))

    monkeypatch.setattr(wall_view, "renderer_bytes_lines", lambda _home: ([], "UNKNOWN"))
    without_evidence = wall_view.render(home, "health")
    assert "a top-pane renderer is unreadable" not in without_evidence
    assert "STATE: GREEN — listed services running" in without_evidence

    monkeypatch.setattr(wall_view, "renderer_bytes_lines", lambda _home: (
        ["RENDERER BYTES: UNKNOWN pin=x unreadable=docs"], "UNKNOWN"))
    with_evidence = wall_view.render(home, "health")
    assert "STATE: UNKNOWN — a top-pane renderer is unreadable" in with_evidence
