from datetime import datetime, timezone, timedelta
import hashlib
import multiprocessing
import json
import os
import subprocess
import pathlib
import pytest
from mishe_tauftauf import post_check, observations
from mishe_tauftauf.feed import Feed


def setup_wall(home):
    from mishe_tauftauf import roles
    roles.install_defaults(home)          # a role is a charter that exists
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




def test_wall_context_includes_only_monitored_role_walls(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall
    (tmp_path / "walls").mkdir(exist_ok=True)
    (tmp_path / "walls" / "genome.md").write_text("Genome plan")
    (tmp_path / "walls" / "health.md").write_text("Health plan")
    (tmp_path / "walls" / "operator.md").write_text("Operator position")
    (tmp_path / "walls" / "audit.md").write_text("Scratch audit")
    (tmp_path / "charters").mkdir(exist_ok=True)
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






def test_invalid_mode_refuses_silently_switching_coordination(tmp_path):
    from mishe_tauftauf import wall
    (tmp_path/"coordination-mode.json").write_text("broken")
    with pytest.raises(ValueError, match="coordination mode"):
        wall.enabled(tmp_path)






































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


def test_wall_outcome_refusal_names_flat_artifact_convention(tmp_path):
    from mishe_tauftauf import wall
    nested = tmp_path / "artifacts" / "field-review" / "note.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("nested evidence\n")
    with pytest.raises(ValueError, match="flat artifacts/NAME file"):
        wall.outcome(tmp_path, "genome", "accepted", "Nested must refuse.", nested)


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






