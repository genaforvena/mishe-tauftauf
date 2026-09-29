---
name: plant-mishe
description: Set up or repair this repository's local self-tending tmux plant with resident genome and witness channels, shared chat.log, and persistent user services. Use for a full local plant; not for the smaller culture-only mishe skill.
---

# Plant mishe locally

Work from this repository root. Read `AGENTS.md` and the current session and service state before changing an existing plant. The setup command is `scripts/plant_local.py`; it creates the site, two resident channels, an operator shell window, and two enabled user services. It preserves existing site charters, launchers, top renderers, handoffs, and unrelated tmux windows. Pass the installed agent command with `--engine-command`; an existing launcher is not replaced by setup.

The site is node-local and gitignored. Keep its chat, charters customized for the node, plans, checks, artifacts, handoffs, and service units there. Put reusable fixes in tracked source, tests, setup scripts, and general instructions. Before committing, confirm `git ls-files .mishe-tauftauf` is empty.

For a fresh plant, run `python3 scripts/plant_local.py --engine-command '<agent command>'`. Use `--home` and `--session` to select another owned plant. Use `--no-services` only for a temporary trial. If the user asked to remove obsolete windows, inspect their pane commands and ownership first, then remove only the named superseded windows after the new channels are live.

Verify the actual user services, advancing top-pane leases, `SYSTEM ZERO` check reports, resident lower panes, and a wake → artifact → exact yield → idle clear in `chat.log`. A service enabled flag or passing unit test alone does not prove the plant is running. Record the site path, session name, three window names, service state, and any pending wake when handing back.
