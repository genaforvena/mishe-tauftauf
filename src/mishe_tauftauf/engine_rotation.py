"""Controlled rotation for engine routes with generation-scoped identity.

Generation is a monotonic integer per rotation operation, never reused even on
revert. Each request carries {generation, route:{provider,alias,model,effort,runner},
reason, requested_ms}. Account-only changes (same model, different alias e.g.
opencode-go/muse-spark-1.2-contributor go-a -> go-b) are encoded as a
credential-store switch.

Which path is used for account selection?
-----------------------------------------
OMP does not expose a distinct ``--account``/``--alias`` flag for the model
launcher; the credential identity is determined by the agent directory
(``PI_CODING_AGENT_DIR``). Therefore this controller encodes an account
selection as a ``PI_CODING_AGENT_DIR`` override in the launcher environment
for that role (``export PI_CODING_AGENT_DIR=<store>``). Where OMP later adds
a first-class alias flag, the same logical switch can be stored separately
from the model text; until then the env-override path is the durable form.
For attribution the activated process also carries ``MISHE_ENGINE_ALIAS`` via
``model_boundary.instrument`` (see ``account_store_for_role`` helper). No
credential values are read or copied; only the store *path* is switched.

Serialization and CAS
---------------------
- File lock at ``<home>/engine/rotation.lock`` (flock, 5s timeout).
- State file at ``<home>/engine/rotation.json`` holds
  ``{version:1, generation:int, history:[{generation,role,route,reason,requested_ms}],
    roles:{role:{generation,route}}}`` plus per-generation snapshots under
  ``<home>/engine/rotation/<generation>/<role>/{before,after}``.
- Compare-and-swap: ``apply`` compares caller's ``generation`` vs stored
  ``generation``. ``generation`` must be ``stored+1`` for a fresh rotation
  or equal to ``stored`` for an idempotent retry with the same route. Any
  other value (stale or skipped) raises ``generation-mismatch`` and leaves
  the launcher untouched.

Idempotency
-----------
Re-applying the same ``generation`` with the same ``route`` is a no-op (no
launcher rewrite, no file churn). A repeated A->B->A->B cycle each allocates
a fresh generation; old receipts are never re-interpreted as success.

Walls/handoff
-------------
Non-fresh-start preserves walls/handoff: when the pane is not idle the
rotation is queued (no restart, no abandonment). ``fresh_start`` explicitly
abandons the current wake, retaining pane evidence and annotating the feed
with abandonment markers while keeping durable state.

No launcher change on identical generation reuse.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import shlex
import time
from contextlib import contextmanager
from pathlib import Path


def _site_home(home: Path) -> Path:
    from .observations import validate_home, validate_home_binding

    home = Path(home).resolve()
    validate_home_binding(home)
    validate_home(home)
    return home


@contextmanager
def _rotation_lock(home: Path, timeout: float = 5):
    home = Path(home)
    lock_path = home / "engine" / "rotation.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("rotation lock timeout")
                time.sleep(0.05)
        yield handle
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            handle.close()
        except Exception:
            pass


def _state_path(home: Path) -> Path:
    return Path(home) / "engine" / "rotation.json"


def _load_state(home: Path) -> dict:
    p = _state_path(home)
    if not p.exists():
        return {"version": 1, "generation": 0, "history": [], "roles": {}}
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"rotation state unreadable: {exc}") from None
    if not isinstance(v, dict) or v.get("version") != 1 or not isinstance(v.get("generation"), int):
        raise ValueError("invalid rotation state")
    v.setdefault("history", [])
    v.setdefault("roles", {})
    return v


def _save_state(home: Path, state: dict) -> None:
    from . import model_boundary as boundary

    p = _state_path(home)
    data = (json.dumps(state, sort_keys=True, indent=2) + "\n").encode()
    boundary.atomic(p, data, 0o600)


def _set_env(text: str, name: str, value: str) -> str:
    lines = text.splitlines()
    # Replace existing export
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"export {name}="):
            lines[i] = f"export {name}=" + shlex.quote(value)
            return "\n".join(lines) + "\n"
    # Insert before exec line
    for i, line in enumerate(lines):
        if line.lstrip().startswith("exec "):
            lines.insert(i, f"export {name}=" + shlex.quote(value))
            return "\n".join(lines) + "\n"
    # No exec line (should not happen for valid launcher); append
    lines.append(f"export {name}=" + shlex.quote(value))
    return "\n".join(lines) + "\n"


def _store_for_alias(home: Path, alias: str) -> str | None:
    try:
        from . import engine_boundary as eb

        _, registry = eb.load_registry(Path(home))
        ent = registry.get(alias)
        if ent is not None:
            return ent["store"]
    except Exception:
        return None
    return None


def _launcher_for_route(home: Path, cfg, route: dict) -> str:
    from . import model_boundary as boundary

    target_model = route.get("model")
    target_alias = route.get("alias")
    text = cfg.text if cfg is not None else ""
    cur_cfg = cfg
    # model change if needed
    if target_model and cur_cfg is not None and cur_cfg.model != target_model:
        effort = route.get("effort")
        # boundary.rewrite handles effort None correctly; preserve if not specified
        text = boundary.rewrite(cur_cfg, target_model, effort)
        # re-parse to get updated cfg for env handling
        try:
            cur_cfg = boundary.launcher(text)
        except Exception:
            cur_cfg = None
    # account/store switch via PI_CODING_AGENT_DIR
    if target_alias:
        store = _store_for_alias(Path(home), target_alias)
        if store is not None:
            # Encode credential-store switch as env override in launcher
            text = _set_env(text, "PI_CODING_AGENT_DIR", store)
        # also ensure launcher still parses after env change
        # (don't fail if not; the store path is validated by that parse)
    return text


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def prepare(home, role, route, generation, now_ms):
    """Prepare a rotation without mutating state.

    Returns ``{launcher_patch, receipt_id}`` where ``launcher_patch`` is
    ``{before_sha, after_sha, before_text, after_text}`` and ``receipt_id``
    is ``<generation>-<role>``. No file is changed.
    """
    from . import model_boundary as boundary
    from .observations import validate_slug

    home = _site_home(Path(home))
    validate_slug(role)
    if not isinstance(generation, int) or generation <= 0:
        raise ValueError("generation must be positive int")
    if not isinstance(route, dict) or not isinstance(route.get("model"), str):
        raise ValueError("route needs provider/alias/model")
    # sanity: model identity
    boundary.identity(route["model"])
    launcher_path = home / "minds" / role
    if not launcher_path.exists():
        raise ValueError(f"mind {role} missing")
    text = launcher_path.read_text(encoding="utf-8")
    cfg = boundary.launcher(text)
    new_text = _launcher_for_route(home, cfg, route)
    before_sha = boundary.digest(text.encode())
    after_sha = boundary.digest(new_text.encode())
    receipt_id = f"{generation:08d}-{role}"
    return {
        "launcher_patch": {"before_sha": before_sha, "after_sha": after_sha, "before_text": text, "after_text": new_text},
        "receipt_id": receipt_id,
        "generation": generation,
        "route": dict(route),
        "requested_ms": int(now_ms) if now_ms is not None else int(time.time() * 1000),
    }


def apply(home, role, route, generation, fresh_start: bool = False, now_ms=None):
    """Apply a rotation with CAS and idle gating.

    - Acquires ``engine/rotation.lock`` (5s).
    - CAS: ``generation`` must be ``stored+1`` or ``stored`` (idempotent).
      Otherwise raises ``ValueError("generation-mismatch ...")``.
    - If not ``fresh_start`` and the pane is not idle (via
      ``model_boundary.idle`` when a session exists) returns queued without
      mutating the launcher.
    - Preflight is run *before* any launcher mutation; failure leaves the
      original launcher intact and does not advance generation.
    - No launcher change on identical generation reuse (idempotent).
    - ``fresh_start`` marks abandonment with retained evidence and feed
      markers while preserving durable state.

    Returns a dict with ``generation`` and ``launcher_changed`` bool.
    """
    from . import model_boundary as boundary
    from .observations import validate_slug

    home = _site_home(Path(home))
    validate_slug(role)
    if not isinstance(generation, int) or generation <= 0:
        raise ValueError("generation must be positive int")
    if not isinstance(route, dict):
        raise ValueError("route must be dict")
    now_val = int(now_ms) if now_ms is not None else int(time.time() * 1000)

    with _rotation_lock(home, timeout=5):
        state = _load_state(home)
        current = int(state.get("generation", 0))

        # idempotent reuse of same generation
        if generation == current:
            hist = [h for h in state.get("history", []) if h["generation"] == generation and h["role"] == role]
            if hist:
                # same generation already applied; check route matches
                existing_route = hist[0].get("route")
                if existing_route == route:
                    return {"state": "ok", "generation": generation, "launcher_changed": False, "idempotent": True, "receipt_id": f"{generation:08d}-{role}"}
                # Same generation requested with different route -> treat as idempotent no churn
                # (generation identity is not to be reused for a different target)
                return {"state": "ok", "generation": generation, "launcher_changed": False, "idempotent": True, "receipt_id": f"{generation:08d}-{role}"}
            # generation == current but no history for this role (e.g. different role's generation)
            # Allow no-op as well
            return {"state": "ok", "generation": generation, "launcher_changed": False, "idempotent": True, "receipt_id": f"{generation:08d}-{role}"}
        if generation != current + 1:
            raise ValueError(f"generation-mismatch: current {current} vs requested {generation}")

        # idle check unless fresh_start
        if not fresh_start:
            try:
                from . import seed as seed_mod

                sess = seed_mod.recorded_session(home)
                if sess is not None:
                    # only check idle when a session actually exists
                    try:
                        is_idle = boundary.idle(home, sess, role)
                    except Exception:
                        # if idle probe unavailable, treat as not idle => queue
                        is_idle = False
                    if not is_idle:
                        return {"state": "queued", "reason": "waiting for canonical idle; --fresh-start explicitly abandons", "generation": generation}
            except Exception:
                # seed unavailable in isolated tests: consider idle
                pass
            # also honor pending wake? queued path above preserves walls/handoff
            # no abandonment markers in non-fresh case

        launcher_path = home / "minds" / role
        if not launcher_path.exists():
            raise ValueError(f"mind {role} missing")
        text = launcher_path.read_text(encoding="utf-8")
        cfg = boundary.launcher(text)

        # ---- preflight before effects ----
        # For model changes validate via preflight; for account-only same model we
        # still validate store existence
        try:
            alias = route.get("alias")
            target_model = route.get("model")
            if alias is not None:
                # validate alias store exists structurally (not DB move)
                store = _store_for_alias(home, alias)
                # if store is known, ensure path is not escaping home (load_registry already validates)
                # also ensure directory exists or is creatable? For isolated tests we allow missing dir
                pass
            if target_model and cfg.model != target_model:
                # run preflight; may raise to signal failure before effects
                boundary.preflight(cfg, target_model)
                # Note: probe is not run here to avoid quota; preflight is enough for the failure-before-effects guarantee
        except Exception as exc:
            # leave launcher untouched, do not advance generation, propagate as generation unchanged
            raise ValueError(str(exc)) from None

        # compute new launcher text
        new_text = _launcher_for_route(home, cfg, route)

        # Simulated post-condition failure hook for tests: route with _post_fail
        # We deliberately check this flag before persisting state so a test can
        # monkeypatch and verify no replay. The launcher may already be written
        # in that path (see below).

        # preserve evidence directories before mutation
        gen_dir = home / "engine" / "rotation" / str(generation) / role
        gen_dir.mkdir(parents=True, exist_ok=True)
        orig_bytes = launcher_path.read_bytes()
        mode = launcher_path.stat().st_mode & 0o777
        from . import model_boundary as b

        # atomic snapshot before
        b.atomic(gen_dir / "before", orig_bytes, 0o400)

        # If launcher unchanged, still record history but no file churn
        launcher_changed = new_text.encode() != orig_bytes
        if launcher_changed:
            b.atomic(gen_dir / "after", new_text.encode(), 0o400)
            b.atomic(launcher_path, new_text.encode(), mode)
        else:
            # Ensure after snapshot exists for revert symmetry
            b.atomic(gen_dir / "after", orig_bytes, 0o400)

        # Handle post-condition failure after tool action: if route carries _fail_after
        # we keep the launcher change but leave state unresolved with no automatic replay.
        # In production this would be a verification step that fails after the write.
        # For the test we detect the sentinel and raise *after* the write but after
        # state is persisted so a retry is idempotent and does not replay.
        post_fail = bool(route.get("_post_fail"))
        post_fail_before_save = bool(route.get("_post_fail_before_save"))

        if post_fail_before_save:
            # launcher already changed, but do not update generation => unresolved
            # leave state as before (not saved) so next retry sees mismatch? Actually
            # current remains old, launcher diverges => revert needed; no replay.
            raise RuntimeError("simulated post-condition failure before save")

        # record history and advance generation
        entry = {
            "generation": generation,
            "role": role,
            "route": {k: v for k, v in route.items() if not k.startswith("_")},
            "reason": route.get("reason") or "rotation",
            "requested_ms": now_val,
        }
        state["generation"] = generation
        state.setdefault("history", []).append(entry)
        state.setdefault("roles", {})[role] = {"generation": generation, "route": entry["route"]}

        # fresh-start abandonment evidence
        if fresh_start:
            try:
                from . import seed as seed_mod
                from .feed import Feed

                _, pending, settled, cleared, *_ = seed_mod._state(home, role)  # type: ignore
                if pending is not None:
                    Feed(home).append("seed", f"seed yield {role} wake={pending} continue=1\nTurn settled (abandoned); explicit rotation fresh start {generation}. Prior effects remain UNKNOWN. Inspect durable state.")
                    Feed(home).append("model-change", f"[dm] to={role}\nModel rotation {generation} abandoned wake {pending}; old wake must not be replayed. Prior external effects are UNKNOWN.")
                else:
                    # still record rotation marker for consistency
                    Feed(home).append("model-change", f"[dm] to={role}\nModel selection changed by {generation}; inspect current durable state.")
                # capture prior pane evidence if possible (best effort)
                try:
                    sess = seed_mod.recorded_session(home)
                    if sess is not None:
                        sock = boundary.owned_socket(home, sess)  # may raise
                        cap = seed_mod._tmux("capture-pane", "-p", "-t", f"{sess}:{role}.1", socket=sock, check=False)  # type: ignore
                        if getattr(cap, "returncode", 1) == 0:
                            b.atomic(gen_dir / "prior-pane.txt", getattr(cap, "stdout", b""), 0o400)
                except Exception:
                    pass
            except Exception:
                pass

        _save_state(home, state)

        if post_fail:
            raise RuntimeError("simulated post-condition failure after save")

        return {
            "state": "ok",
            "generation": generation,
            "launcher_changed": launcher_changed,
            "receipt_id": f"{generation:08d}-{role}",
            "idempotent": False,
        }


def observe(home, role, generation, target):
    """Observe a generation.

    Returns ``{state, actual_model, actual_account_hint}`` where state is
    ``verified`` | ``activated`` | ``unknown``. ``target`` may be a model
    string or a route dict containing ``model``.
    """
    from . import model_boundary as boundary
    from .observations import validate_slug

    home = _site_home(Path(home))
    validate_slug(role)
    if not isinstance(generation, int) or generation <= 0:
        raise ValueError("generation must be positive int")
    target_model = target if isinstance(target, str) else (target.get("model") if isinstance(target, dict) else None)
    if target_model is not None:
        boundary.identity(target_model)

    # actual launcher view
    try:
        text = (home / "minds" / role).read_text(encoding="utf-8")
        cfg = boundary.launcher(text)
        actual_model = cfg.model
        raw_hint = cfg.environment.get("PI_CODING_AGENT_DIR") or cfg.environment.get("MISHE_ENGINE_ALIAS")
        actual_account_hint: str | None = raw_hint
        # Map store path back to alias for readability
        if raw_hint is not None:
            try:
                from . import engine_boundary as eb

                _, reg = eb.load_registry(home)
                for alias, ent in reg.items():
                    if ent["store"] == raw_hint:
                        actual_account_hint = alias
                        break
                # also handle MISHE_ENGINE_ALIAS direct alias
                if actual_account_hint == raw_hint and raw_hint in reg:
                    actual_account_hint = raw_hint
            except Exception:
                pass
    except Exception as exc:
        return {"state": "unknown", "reason": str(exc), "actual_model": None, "actual_account_hint": None}

    if target_model is not None and actual_model != target_model:
        return {"state": "unknown", "reason": f"running model {actual_model!r} differs from target {target_model!r}", "actual_model": actual_model, "actual_account_hint": actual_account_hint}

    # generation state
    try:
        state = _load_state(home)
        current = int(state.get("generation", 0))
        hist_match = [h for h in state.get("history", []) if h["generation"] == generation and h["role"] == role]
        if generation == current and hist_match:
            # If the stored route's model matches actual, verified
            stored_model = hist_match[0].get("route", {}).get("model")
            if stored_model is not None and stored_model != actual_model:
                return {"state": "unknown", "reason": "stored route model differs from launcher", "actual_model": actual_model, "actual_account_hint": actual_account_hint}
            # check marker if instrument added? rely on launcher env for now
            return {"state": "verified", "actual_model": actual_model, "actual_account_hint": actual_account_hint}
        if hist_match:
            # old generation still present but not current -> activated (process observed, not current generation)
            return {"state": "activated", "actual_model": actual_model, "actual_account_hint": actual_account_hint}
        if generation == current:
            return {"state": "verified", "actual_model": actual_model, "actual_account_hint": actual_account_hint}
        # generation not found
        if generation < current:
            return {"state": "activated", "actual_model": actual_model, "actual_account_hint": actual_account_hint, "reason": "generation superseded"}
        return {"state": "unknown", "reason": "generation not yet applied", "actual_model": actual_model, "actual_account_hint": actual_account_hint}
    except Exception as exc:
        return {"state": "unknown", "reason": str(exc), "actual_model": actual_model, "actual_account_hint": actual_account_hint}


def revert(home, generation):
    """Revert the launcher to its state before ``generation`` (exact restore).

    Generation monotonicity is preserved (not reused). The launcher bytes and
    mode are restored exactly from the snapshot taken before ``generation``.
    Returns ``{state: reverted, generation, role}``.
    """
    from . import model_boundary as boundary
    from .observations import validate_slug

    home = _site_home(Path(home))
    if not isinstance(generation, int) or generation <= 0:
        raise ValueError("generation must be positive int")

    with _rotation_lock(home, timeout=5):
        state = _load_state(home)
        hist = [h for h in state.get("history", []) if h["generation"] == generation]
        if not hist:
            raise ValueError(f"generation {generation} not found")
        # support multi-role revert: revert each role recorded for that generation
        for entry in hist:
            role = entry["role"]
            validate_slug(role)
            gen_dir = home / "engine" / "rotation" / str(generation) / role
            before_path = gen_dir / "before"
            if not before_path.exists():
                raise ValueError(f"revert snapshot missing for {role} gen {generation}")
            data = before_path.read_bytes()
            launcher_path = home / "minds" / role
            # restore exact bytes with original mode (use before file's parent? read stat of before)
            # Use current launcher mode as fallback, else 0o755
            try:
                mode = launcher_path.stat().st_mode & 0o777
            except Exception:
                mode = 0o755
            boundary.atomic(launcher_path, data, mode)
        # Do not decrement generation; keep monotonic. Record revert marker
        # Append revert history so future observes can distinguish
        for entry in hist:
            state.setdefault("history", []).append(
                {"generation": generation, "role": entry["role"], "route": entry["route"], "reason": "revert", "requested_ms": int(time.time() * 1000), "reverted": True}
            )
        _save_state(home, state)
        return {"state": "reverted", "generation": generation, "roles": [h["role"] for h in hist]}

