# Getting started

[README](../README.md) · [Mesh](mesh.md)

This sets up one plant in a checkout, **held**, and then lets it run. Nothing calls
a model until you release the hold.

## 1. Prerequisites

- Linux with a working user systemd manager (`systemctl --user status`), Python 3.10+
  and Git.
- At least one authenticated coding agent: **`claude`** (Claude Code) and/or **`omp`**.
  Mishe supplies neither the model nor credentials; usage counts against your plan.
- Optional: GitHub CLI `gh` with repository access, for the CI reading. Without it
  the CI signal stays UNKNOWN.

Waking agents run with your account's access and can change the checkout. This is
not an OS sandbox; use a repository and account you intend them to work in.

## 2. Install and create the site

```sh
git clone https://github.com/genaforvena/mishe-tauftauf.git
cd mishe-tauftauf
export PATH="$PWD/bin:$PATH" MISHE_SEED_HOME="$PWD/.mishe-tauftauf"   # no install, no third-party packages
./bin/mishe init                            # creates the ignored site and default charters
```

The site holds everything local (walls, signals, chat, wake files) and is never
committed. The default roles are `genome`, `witness`, `senses`, `discover`, `health`,
`docs` and `research-methods`; each is a charter in `SITE_HOME/charters/`.

## 3. Choose engines

By default a role wakes on `claude` with `claude-haiku-5-5`. To change one, write
`SITE_HOME/engines/ROLE.json` (for example `{"engine": "claude", "model": "claude-haiku-5-5"}` or `{"engine": "omp", "model": "..."}`; only these two engines exist).

## 4. Install the triggers — held

```sh
mishe hold on --reason "first install"
mishe sync --repo "$PWD" --pythonpath "$PWD/src"
mishe doctor                # exits 0 when units match what was generated
```

`sync` writes the user units (`mishe-wake@.service`, a path unit per role, producer
timers, `mishe-failed@`, the doctor) and enables the paths and timers.
`systemctl --user list-units 'mishe*'` shows them.

## 5. Start

```sh
mishe hold off              # releases the hold and offers the first owed role
```

Owed means something happened that the role has not seen: a message, a signal that
changed, a follow-up that came due. On a fresh site nothing is owed, so nothing runs
until you create work, for example:

```sh
mishe wall dm --source operator --to genome --text "Please read the README and tell me what is unclear."
```

That touches genome's inbox; its path unit starts one wake. Watch it:

```sh
journalctl --user -u mishe-wake@genome.service -f
ls SITE_HOME/wakes/ ; cat SITE_HOME/dispatch/genome.json
```

A healthy wake ends with the dispatch `completed`, the wall updated and the message
read. Cost and turns are in the wake's `*.out.json` (claude).

## Writing a wall, an outcome, a message

A woken mind does these itself, and ends every wake with `mishe yield --result changed|verified|blocked`
(its updated wall on stdin). By hand:

```sh
mishe wall write --owner ROLE --file NOTES        # replaces the role's wall (size-limited)
mishe wall dm --source ROLE --to ROLE2 --text "..." # addressed message on the shared tape
mishe wall outcome --owner ROLE --kind accepted --file NOTES --evidence PATH
```

An outcome needs evidence the recorder can read; what counts is below.

## Recording a contribution, and the evidence it accepts

A wall note records what a mind is doing now. A *wall outcome* is the separate,
durable record of a contribution that actually landed, and it is what the plant
counts. One outcome per contribution:

```bash
mishe wall outcome --owner ROLE --kind accepted \
  --file NAME.md --evidence "$MISHE_SEED_HOME/artifacts/NAME.md"
```

`--file` names the notes the role has already staged, and the stage is
per-role: a bare `NAME.md` resolves to `SITE_HOME/walls/ROLE-NAME.md`, so one role
cannot read another's staged notes. A path with a directory keeps its
working-directory meaning, and an absolute path is never re-anchored.

`--kind` says what kind of contribution this is, and there are exactly four:

| Kind | Use it for |
| --- | --- |
| `accepted` | The contribution landed and stands. |
| `blocker-resolved` | A blocker that was blocking work is now cleared. |
| `blocker-retired` | The blocker is withdrawn or no longer applies, so it is closed rather than solved. |
| `hypothesis-changed` | The finding changed what was believed. |

The distinction between `blocker-resolved` and `blocker-retired` is the one
worth making: a resolved blocker was removed by something that worked, and a
retired one stopped applying. Any other value is refused before anything is
recorded, so a typo cannot become a permanent category.

These four are the whole vocabulary, and they are **labels, not verdicts**.
Nothing in the plant re-derives them: a `--kind accepted` outcome is the
author's report that the contribution landed, and the counts that read them
are author reports too. Choose the one that describes the contribution; the
evidence file, not the label, is what the plant can check.

The evidence file has a shape, and the shape is checked before anything is
recorded. It must be **one regular file directly inside** the site's
`artifacts/`, or a `discovery/scan-*.json`, or a `dms/*.md`:

- A file in a subdirectory, such as `artifacts/wake-1234/evidence.md`, is
  refused. The rule counts directory levels, so only files sitting directly in
  the named directory qualify.
- A relative path is resolved against the current working directory first.
  When that form does not name an eligible file in the site, the same relative
  path is resolved against the site home instead, so `artifacts/NAME.md` from
  the repository root names the **site's** `artifacts/NAME.md` and is accepted.
  A path that is eligible under neither form is refused, and the refusal names
  the path it resolved to. Give the absolute site path if you want no
  ambiguity.
- Each outcome binds its evidence file. Reusing one file for a second outcome
  is refused with `outcome evidence path already bound: ...; each outcome binds its own
  flat artifacts/NAME file (bind a copy of the final bytes), or re-evidence with
  --supersedes <record digest>`. Copy the final bytes to a new flat file for a new
  outcome, or publish a superseding outcome that names the record digest being
  re-evidenced (see below).
A refusal is not a lost contribution: the reason is appended to
`SITE_HOME/logs/outcome-refusal-audit.jsonl`, which is also where to read how many
refusals happened and which role hit them. Read that log before writing a
second artifact — the shape of the file, not the effort behind it, is what the
refusal reports.

That log holds **refusals only**: a row exists because a publication did not
happen, so it never carries a decision field. The accepted side is a different
file — `SITE_HOME/records/`, one `wall-outcome` record per publication. Counting
both sides per role means joining the two: the audit gives refusals, the record
store gives accepted contributions. The two files do not share a time window
field, so say which window you compared when you report the numbers.

## Stop

```sh
mishe hold on                         # no more model calls; units stay installed
mishe sync --uninstall                # remove the generated units
```
