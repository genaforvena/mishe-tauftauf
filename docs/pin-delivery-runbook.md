# Pin-delivery signal runbook

The steps below read the `pin-delivery` signal when its reading is unclear. They were moved here from [Wall coordination](wall-coordination.md) so that the coordination rules stay readable.

When the check writes its reason to `signals/pin-delivery.detail`, read that file and the producer journal. Run
`mishe signal show pin-delivery` for the effective reading. An
unchanged result refreshes `signals/pin-delivery.seen` and leaves the last edge in
`signals/pin-delivery.json`. The detail and `.ran` timestamp can advance when a
check cannot establish a fresh reading. The last reading expires after ten
minutes without a successful observation. During a promotion the checker
still inspects the release that `runtime/current` points to. If that release
directory has no runtime package, a running checker reports RED immediately.
It also reports RED if `src/mishe_tauftauf/__main__.py` is missing, has a
syntax error, or makes no call at import time. An import alone dispatches
nothing, and neither does a builtin such as `print(...)` or a method on a
literal such as `''.join([])`; a name the module binds itself (for example
`from .cli import help`) is a real call. A missing or unreadable pointer yields
no fresh observation.
Genome subscribes to changes in `pin-delivery`. If a healthy sample arrives
during Genome's 120-second wake debounce (`wake_debounce_seconds`), Genome's
dossier, read when the wake starts, can show GREEN. Each sample replaces the
detail file. To recover the RED sample's reason, read the producer
journal back to the time of the alert.

```sh
journalctl --user -u mishe-producer-pin-delivery.service --since '1 hour ago' \
  --grep 'mishe: pin-delivery: exit=2 detail=' --no-pager
```

If the producer's configured interpreter cannot start the `mishe_tauftauf`
module, the checker never runs and the last reading can remain visible until
its freshness expires. The failure reporter can fail at the same startup step
if both services depend on the damaged release. Read both unit journals without
the checker exit filter. Set the time range to cover the failure.

```sh
journalctl --user -u mishe-producer-pin-delivery.service \
  -u mishe-failed@mishe-producer-pin-delivery.service.service \
  --since '1 hour ago' --no-pager
```

The pinned CLI can exit successfully while skipping the unit's command. A
retained checker can still report GREEN when the entry point calls an unrelated
imported function or calls `main(["--help"])` and exits after printing usage.
A class body or comprehension that binds a builtin's name in its own scope
does not shadow the module-level builtin; the checker counts that call as the
builtin and reads RED.
Use the journal to match the installed producer's `outcome=exit=0` line
and `_CMDLINE` to the release's checker path. Match the application's
`_SYSTEMD_INVOCATION_ID` to the manager's `USER_INVOCATION_ID` on the
`Finished` line. The checker outcome is logged before the producer writes
the signal, and another invocation can advance the shared
`signals/pin-delivery.seen` stamp. Capture its mtime and ctime before the
next sample replaces them. The mtime holds the observation time sampled at
CLI startup. The ctime shows when the file metadata last changed. A producer
that started earlier can finish last and move the mtime backward even when
both units succeed. Confirm the stamp changed during the matching invocation.
If another writer could account for the stamp, keep the producer observation
UNKNOWN.

```sh
journalctl --user -b -u mishe-producer-pin-delivery.service --since '1 hour ago' \
  -o json --output-fields=MESSAGE,_CMDLINE,_SYSTEMD_INVOCATION_ID,USER_INVOCATION_ID --no-pager
stat -c 'freshness %y, file changed %z' "$MISHE_SEED_HOME/signals/pin-delivery.seen"
```

The delivery record stores the initial `mishe-arm.service` start in
`consumer_invocation.started_monotonic`. The post-drain arm has a separate
`rearm.started_monotonic` in the `observe.output` text. Both fields count
microseconds since boot. The CLI prints the due epoch or `None` after it arms.
Successive runs can print the same value.

```sh
systemctl --user show mishe-arm.service -p ExecMainStartTimestampMonotonic -p InvocationID
journalctl --user -b --no-pager --since '1 hour ago' -o short-monotonic -u mishe-arm.service
```

When the current start matches the recorded one, filter the journal by
`_SYSTEMD_INVOCATION_ID` using the ID from the same `systemctl show` output.
For an earlier start, match the recorded time to the journal's `Starting`
and `Finished` lines for that invocation and look for the application output
between them. Select the boot and time range that cover activation. A skipped
command can exit successfully. Treat absent or ambiguous output as UNKNOWN.

The `signal-coverage` duty also starts through the pinned CLI. From an
independent launcher it can flag a missed cadence when both the `.seen` and
`.ran` stamps age past the limit. A retained pin producer can refresh both
shared stamps, so coverage can show GREEN while the installed pin producer
still skips its checker.

The due timer and the `signal-expiry` watchdog also launch the CLI. If both use
the damaged release, the timer can skip publishing the expiry edge and the
watchdog can skip its check. A working reader can still show UNKNOWN after ten
minutes. Check `signals/pin-delivery.json` for the expiry edge and the
`mishe-wake@genome.service` journal for an engine start. The wake and its path
debounce can share the damaged entry point.
