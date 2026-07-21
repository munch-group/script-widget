# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working
with code in this repository.

Project context for `script-widget` -- a Jupyter cell magic that runs a cell
in a fresh, isolated namespace, built on
[anywidget](https://anywidget.dev).

## What this is

Tag a cell `%%exercise` and its body runs in a namespace that is fresh on
every invocation and discarded afterward -- nothing the cell defines or
imports persists past that one run, and nothing the notebook has already
defined is visible to it *except* through the cell's own
`import`/`from ... import` statements. Because Python caches loaded modules
in `sys.modules`, such an import is essentially free and hands back the
exact same live object the notebook already has (the same `plt`, with the
same open figures; the same model, with the same weights) -- nothing is
reloaded, reinitialized, or (fully) restarted. Everything the cell produced
-- stdout, stderr, rich display output, any exception -- is shown in a
shaded `ExerciseOutputWidget` box below the cell instead of the notebook's
normal (unstyled) output area.

The repo was scaffolded from the `munch-group` Python-library template
(pixi environment, quartodoc docs, conda/PyPI release automation) -- the
same template `puzzle-widget`/`steps-widget`/`turtle-widget` use. Like
`puzzle-widget`, there is no separate console-script entry point (no CLI
use case) -- the whole package is the widget + magic, and the exec/capture
logic is split out into its own `executor.py` for the same reason
`puzzle-widget` splits out `checker.py`: it's independently useful and
(mostly) unit-testable without a live IPython shell.

Note: the package and repo are still named `script_widget`/`script-widget`
even though the magic itself is `%%exercise` -- that mismatch is
deliberate, not leftover from a partial rename (a full package/repo rename
would touch the repo's identity: folder location, PyPI/conda package name,
GitHub URLs -- out of scope for a magic-name change alone).

## Package layout

The package is `script_widget` under `src/`:

- `src/script_widget/executor.py` -- the engine. `run_exercise(source,
  ip=None)` `ast.parse`s the cell body, execs every statement but the last
  in a **fresh, empty namespace** (no snapshot of the notebook's `user_ns`
  at all -- see "Architecture" below for why), then handles the last
  statement like a real notebook cell: a bare expression's value gets
  displayed, an assignment still runs but produces no output. With a live
  `ip`, capture goes through `IPython.utils.capture.capture_output` (stdout,
  stderr, and rich display output all at once) and `ip.events.trigger(
  "post_execute")` is called after running so any matplotlib-inline flush
  hook fires exactly as it would after a normal cell. Without `ip` (plain
  `pytest`), only stdout/stderr are captured via `contextlib.redirect_*`
  and the trailing value (if any) is reported as a `text/plain` output.
  Returns an `ExerciseResult(stdout, stderr, outputs, error, traceback)` --
  never raises; a `SyntaxError` or any runtime exception is caught and
  reported via `.error`/`.traceback`.
- `src/script_widget/widget.py` -- `ExerciseOutputWidget` (the
  `anywidget.AnyWidget`) + the embedded `_ESM`/`_CSS` frontend strings +
  `register_exercise_magic()`. The widget's traits are copied from an
  `ExerciseResult` once at construction and never change afterward (unlike
  `puzzle_widget.PuzzleWidget`, there's no interactive re-checking), so the
  frontend's `render()` draws once from the model's initial state with no
  change listeners. The embedded CSS's class prefix (`sw-*`) tracks the
  package name (`script_widget`), not the magic name -- it's an internal
  styling namespace, not user-facing.
- `src/script_widget/__init__.py` -- re-exports `ExerciseResult`/
  `run_exercise`/`ExerciseOutputWidget`/`register_exercise_magic` from
  `executor.py`/`widget.py`. Importing `script_widget` therefore both
  exposes the public API and registers the cell magic as a side effect.
- `test/` -- headless pytest suite: `test_executor.py` (the engine, split
  into a no-shell fallback-path section and an `IPython.testing.globalipapp`
  section for the `capture_output`/`post_execute` paths that genuinely need
  a live shell), `test_widget.py` (`ExerciseOutputWidget` + the magic --
  registration wiring is tested against a hand-rolled fake shell, not a
  real one, to stay deterministic and isolated from other tests' IPython
  singleton state), `test_frontend.py` (`node --check` on the extracted
  `_ESM`, skipped if `node` isn't on `PATH`).
- `docs/` -- Quarto + quartodoc site (`docs/pages/*.ipynb` prose,
  `docs/api/*.qmd` API ref -- the checked-in `.qmd` files are placeholders;
  run `pixi run api` to regenerate them from the live docstrings).
- `pyproject.toml` -- packaging metadata **and** the pixi workspace (deps +
  task runner).
- `conda-build/`, `.github/workflows/` -- conda/PyPI release on tag push.
- `scripts/` -- version-bump / docs-build / release helpers invoked by pixi
  tasks.

## Architecture

### Why a fresh namespace, not a snapshot of the notebook's

An earlier design considered `exec`-ing against `dict(ip.user_ns)` (a
shallow copy of the notebook's namespace) so a cell could read *any*
already-defined name, not just imports. That was deliberately dropped in
favor of a genuinely empty namespace: it makes each `%%exercise` cell
self-contained and reproducible -- what a cell depends on is visible at the
top of the cell (its own imports), not implicit in whatever the notebook
happens to have accumulated by the time it runs. The tradeoff is real and
intentional: a plain notebook variable with no import path (a dataframe
built inline, a model trained ad hoc and only ever bound to a local name)
is **not** visible to `%%exercise`, full stop -- there is no fallback.

What still reads as "the isolated cell has access to already-imported
libraries/models" is Python's own `sys.modules` cache: `import matplotlib.
pyplot as plt` inside the cell doesn't re-run matplotlib's setup or lose
any state -- it's a cache hit that hands back the identical module object
the notebook already imported. `test_executor.py::
test_import_inside_the_cell_returns_the_same_live_module` pins this down
directly (mutate an attribute on a module via a re-import inside
`run_exercise`, assert the mutation is visible on the original object).

### The matplotlib-figure-flush trick

`capture_output`'s display-publisher swap captures explicit `display(...)`
calls and the trailing expression's auto-display, but **not** a bare
`plt.plot(...)` with no `.show()`/`display()` call -- that auto-flush
normally happens via a `post_execute` hook the inline backend registers on
the shell, which never fires for a bare `exec()` call outside `ip.
run_cell`. `run_exercise` calls `ip.events.trigger("post_execute")` itself,
still inside the `capture_output` block, right after running the cell --
this reuses whatever hook the notebook's own `%matplotlib inline` already
registered (a no-op if inline mode isn't active, matching normal notebook
behavior in that case too). It's called in a `finally`, so a figure drawn
before an exception still gets flushed and shown, exactly like a real cell.

## Gotchas

- **Mutating a shared object via an import IS visible outside the cell.**
  The isolation is about name *bindings* in the exec namespace, not deep
  object identity -- `plt.rcParams[...] = ...` inside `%%exercise` really
  does persist, because `plt` is the same cached module object both inside
  and outside the cell. This is required for the feature to be useful (a
  figure drawn inside `%%exercise` needs to use the real matplotlib state)
  but is the *only* channel by which a cell can affect anything outside
  itself.
- **No IPython input transforms inside the cell body.** `%%exercise` parses
  with plain `ast.parse`/`exec`, not `ip.run_cell`, so line magics
  (`%time`), shell escapes (`!ls`), and `?`-help don't work inside the cell
  -- plain Python only.
- **`IPython.testing.globalipapp`'s shell restricts rich display to
  `text/plain` by default.** `TerminalInteractiveShell` (reasonably, for a
  real terminal) sets `display_formatter.active_types = ["text/plain"]`,
  which silently drops `image/png` etc. even though the matplotlib-inline
  formatter is registered and would produce real PNG bytes if called
  directly. `test_executor.py`'s `ip` fixture explicitly widens
  `active_types` back to `format_types` so the test shell behaves like a
  real Jupyter kernel -- forget this and figure-capture tests fail with
  only a `text/plain` repr of the `Figure` object, not a missing output.
- **A live IPython shell is process-global, not per-test.** `IPython.
  get_ipython()` returns whatever `IPython.testing.globalipapp.get_ipython()`
  registered anywhere earlier in the same pytest process -- so a test that
  wants to verify the *absence* of a shell (`register_exercise_magic(
  ipython=None)` should return `False`) can't rely on ambient state; it
  must monkeypatch `script_widget.widget.get_ipython` directly instead.
- **The package/magic name mismatch is intentional, not a leftover.** The
  package stays `script_widget`; the magic is `%%exercise`. Don't "fix"
  this by renaming the package -- that's a separate, deliberately-deferred
  decision (see "What this is" above).

## Environment & commands

Pixi-managed (config in `pyproject.toml` under `[tool.pixi.*]`; channels
`conda-forge` + `munch-group`; platforms `osx-arm64`, `linux-64`). Python
`>=3.9,<3.14`. Key deps: `anywidget` (0.11.x), `traitlets`, `ipython`,
`nodejs` (for the `node --check` frontend test), `matplotlib-base`
(dev/test only, for the figure-capture tests), `quarto`/`quartodoc`,
`pytest`.

- Dev install: `pixi run install-dev` (editable, no build isolation).
- Run tests: `pixi run test` (== `pytest test/`).
- Run a single test: `pixi run pytest test/test_executor.py::test_exception_is_caught_and_reported`
  (or plain `pytest ...` -- `test/conftest.py` puts `src/` on `sys.path`, so
  this works even without `install-dev`).
- Try the widget: open a notebook, `import script_widget`, then
  ```
  %%exercise
  import matplotlib.pyplot as plt
  plt.plot([1, 2, 3])
  ```
- JS syntax check after editing the embedded frontend string:
  ```bash
  python -c "from script_widget import widget as m; open('/tmp/e.mjs','w').write(m._ESM)"
  node --check /tmp/e.mjs
  ```
  (also `test_frontend.py::test_esm_is_syntactically_valid_js`, run
  automatically by `pixi run test` whenever `node` is on `PATH`).
- Build docs: `pixi run api` (quartodoc API pages), then `pixi run docs`
  (execute the doc notebooks in place).
- Release: `pixi run bump` / `release` / `version` drive
  `scripts/bump_version.py` + a tag push, which triggers the conda/PyPI
  workflows.
- Code review: `/review <target-file>` fans out specialist subagents
  (Python, frontend, API consistency, UI heuristics, theming) into a
  prioritized report under `.claude/review-reports/`; `/review-apply`
  applies that report's findings in small, test-gated, one-commit-per-batch
  steps. See `review-kit/README.md` for the full workflow.

## Distribution

Both `.github/workflows/conda-release.yml` and `pypi-release.yml` trigger on
version tag pushes (`vX.Y[.Z][.rcN]`), building on `macOS-latest` and
`ubuntu-latest` (no `win-64` in this workspace's platform list) and pure
Python wheel to PyPI, mirroring `puzzle-widget`'s setup.

## Testing approach

- `executor.run_exercise` is split into a no-shell fallback path (pure
  stdlib -- `ast`, `contextlib`, `traceback`) tested without any IPython
  dependency, and an `IPython.testing.globalipapp`-backed section for the
  `capture_output`/`post_execute` paths that genuinely need a live shell
  (rich `display()` capture, matplotlib figure flush, flush-on-exception).
- `ExerciseOutputWidget` is tested by constructing it directly from an
  `ExerciseResult` -- no magic/IPython involved.
- `register_exercise_magic`'s registration wiring is tested against a
  hand-rolled fake shell rather than a real `InteractiveShell`, to stay
  deterministic regardless of other tests' global IPython singleton state.
- The actual shaded-box rendering isn't (and can't be, headlessly)
  exercised by the pytest suite -- `test_frontend.py` only checks the
  extracted `_ESM` parses as valid JS. Manually verify the rendered output
  against a live notebook after editing `_ESM`/`_CSS`.
