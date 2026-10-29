---
name: ai-bar-quality
description: Apply proportionate engineering checks when changing ai-bar, inspired by the work/right/practice structure of unclebob/ub-mode. Read the repository AGENTS.md first.
---

# ai-bar quality workflow

Use this skill for nontrivial changes to ai-bar. The repository's `AGENTS.md` is authoritative for architecture, security, platform constraints, and exact verification commands. This is an original adaptation inspired by https://github.com/unclebob/ub-mode; do not treat it as a verbatim copy.

## 1. Make it work

1. Read `AGENTS.md`, `README.md`, the relevant modules and existing tests. Inspect the current diff and preserve user changes.
2. State the intended observable behavior and the smallest implementation that delivers it. Challenge requirements that unnecessarily complicate the implementation.
3. For bugs, reproduce the failure with a focused regression test before fixing it, when feasible. For features, write a behavior-focused test first. Never add a test that cannot detect a broken implementation.
4. Keep configuration data and validation centralized in `ai_bar/config.py`; synchronize `config.example.json` and documentation when public options change.
5. Keep GTK object access on the main thread; use `GLib.idle_add` to return from workers. Preserve lifecycle cleanup, page identity maps, optional integrations, and existing X11/Wayland boundaries.
6. Choose the smallest coherent change. Never perform unrelated refactoring.

## 2. Make it right

1. Run focused tests for affected behavior, then the full project test suite as the environment allows.
2. Investigate root causes rather than silencing failures. Remove duplication when it is real, but do not create unnecessary abstractions.
3. Test user-visible behavior and state transitions, not just internal function calls. For pages, test attach/detach/return; for configuration, test defaults/merge/rejection; for session scripts, do not trigger real power actions.
4. For a significant algorithmic or security-sensitive change, consider additional complexity, coverage, duplication, or mutation analysis **if suitable tools are installed**. Such optional tools are not prerequisites for routine edits; never claim their metrics passed without running them.
5. Check security boundaries: same-origin credential/favicons rules, keyring-only secrets, no passwords in configuration/logs/tests, and authorization of session actions.
6. Refactor only after behavior has been pinned down by tests.

## 3. Practice and verify

Apply checks in proportion to the change:

| Change size | Minimum gate |
| --- | --- |
| Documentation only | Review links/instructions, `git diff --check` |
| Local Python fix | Focused regression test, affected module compilation, `git diff --check` |
| Cross-module or config change | Relevant tests plus full unittest suite, `compileall`, documentation/example sync |
| UI, X11, tray, VTE or WebKit change | Automated tests plus focused manual smoke test when a suitable display is available |
| Installer/session shell change | `bash -n` on changed shell scripts and `tests/test_packaging.py`; never reboot or log out in tests |

Repository-wide standard commands (run from root):

```sh
python3 -m unittest discover -s tests
python3 -m compileall ai_bar tests
git diff --check
```

When the suite requires an X display and none is available:

```sh
xvfb-run -a python3 -m unittest discover -s tests
```

- Keep commits small and descriptive, usually on a working branch. Do **not** push straight to `main` unless explicitly requested; prefer a PR for review.
- Summarize what changed, why, tests actually run and results, untested risks, and any follow-up work.
- Do not run expensive analyses or broaden scope just to satisfy a metric.
