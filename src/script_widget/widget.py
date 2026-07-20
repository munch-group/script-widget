"""script_widget.widget
=======================

A Jupyter widget that runs a cell body in a fresh, isolated namespace (see
``executor.py``) and shows everything it produced -- stdout, stderr, rich
display output, and any exception -- in a shaded box below the cell,
instead of the notebook's normal (unstyled) output area.

* Built on `anywidget` (same convention as ``puzzle-widget``), so it
  behaves the same across VS Code notebooks, JupyterLab, Notebook 7 and
  Colab.
* All execution/capture logic lives in ``executor.py``; this module only
  wraps the result in a synced-traitlets widget and wires up the
  ``%%exercise`` cell magic.

Usage
-----
    import script_widget  # registers the %%exercise cell magic

    %%exercise
    import matplotlib.pyplot as plt
    plt.plot([1, 2, 3])
"""

from __future__ import annotations

import anywidget
import traitlets

from .executor import run_exercise

try:  # IPython is present whenever a kernel is running, but guard anyway.
    from IPython import get_ipython
    from IPython.display import display as _ipy_display
except Exception:  # pragma: no cover
    def get_ipython():
        return None

    def _ipy_display(*a, **k):
        pass


__all__ = ["ExerciseOutputWidget", "register_exercise_magic"]


# --------------------------------------------------------------------------- #
# Frontend (ESM + CSS) -- no external dependencies, offline-safe.             #
# --------------------------------------------------------------------------- #

_ESM = r"""
/** HTML-escape helper -- unused for text (textContent escapes automatically)
 *  but kept for any future spot that needs to build an HTML string directly. */
function esc(s){
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

/** MIME types in the order we prefer to render them, richest first --
 *  same preference order Jupyter itself uses for a display mimebundle. */
const MIME_PRIORITY = ["image/png", "image/jpeg", "image/svg+xml", "text/html", "text/plain"];

function pickMime(data){
  for (const mime of MIME_PRIORITY){
    if (mime in data) return mime;
  }
  const keys = Object.keys(data);
  return keys.length ? keys[0] : null;
}

/** Build a DOM node for one captured rich-display MIME bundle, picking the
 *  richest available representation. Returns null for an empty bundle. */
function renderOutput(data){
  const mime = pickMime(data);
  if (mime == null) return null;
  const value = data[mime];

  if (mime === "image/png" || mime === "image/jpeg"){
    const img = document.createElement("img");
    img.className = "sw-image";
    img.src = "data:" + mime + ";base64," + value;
    return img;
  }
  if (mime === "image/svg+xml"){
    const wrap = document.createElement("div");
    wrap.className = "sw-svg";
    wrap.innerHTML = value;
    return wrap;
  }
  if (mime === "text/html"){
    const wrap = document.createElement("div");
    wrap.className = "sw-html";
    wrap.innerHTML = value;
    return wrap;
  }
  const pre = document.createElement("pre");
  pre.className = "sw-text";
  pre.textContent = Array.isArray(value) ? value.join("") : String(value);
  return pre;
}

/**
 * anywidget render entry point. An %%exercise result is fully determined at
 * widget-construction time and never changes afterward (unlike PuzzleWidget,
 * there's no interactive re-checking), so this draws once from the model's
 * initial state -- no change listeners needed.
 */
function render({ model, el }){
  const root = document.createElement("div");
  const success = model.get("success");
  root.className = "sw-root " + (success ? "sw-ok" : "sw-error");

  const badge = document.createElement("div");
  badge.className = "sw-badge";
  badge.textContent = (success ? "\u{1F512}" : "⚠️") + " isolated exercise";
  root.appendChild(badge);

  const stdout = model.get("stdout");
  if (stdout){
    const pre = document.createElement("pre");
    pre.className = "sw-stdout";
    pre.textContent = stdout;
    root.appendChild(pre);
  }

  const stderr = model.get("stderr");
  if (stderr){
    const pre = document.createElement("pre");
    pre.className = "sw-stderr";
    pre.textContent = stderr;
    root.appendChild(pre);
  }

  (model.get("outputs") || []).forEach((data) => {
    const node = renderOutput(data);
    if (node) root.appendChild(node);
  });

  const tb = model.get("traceback");
  if (tb){
    const pre = document.createElement("pre");
    pre.className = "sw-traceback";
    pre.textContent = tb;
    root.appendChild(pre);
  }

  el.appendChild(root);
}

export default { render };
"""

_CSS = r"""
/* Styles for the DOM built in _ESM's render(): a shaded, left-accented box
   (.sw-root) so %%exercise output reads as visually distinct from a normal
   cell's plain output -- .sw-badge, then any of .sw-stdout/.sw-stderr/
   rendered outputs (.sw-image/.sw-svg/.sw-html/.sw-text)/.sw-traceback. */
.sw-root { display: flex; flex-direction: column; gap: 8px;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  background: #f4f0ff; border-left: 4px solid #7c3aed; border-radius: 6px;
  padding: 10px 14px; }
.sw-root.sw-error { background: #fef2f2; border-left-color: #b91c1c; }
.sw-badge { font-size: 12px; font-weight: 600; letter-spacing: .02em; color: #5b21b6; }
.sw-root.sw-error .sw-badge { color: #b91c1c; }
.sw-stdout, .sw-stderr, .sw-text, .sw-traceback {
  margin: 0; padding: 8px 10px; border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, "Cascadia Code", Menlo, monospace;
  font-size: 12.5px; white-space: pre-wrap; word-break: break-word; }
.sw-stdout, .sw-text { background: #ffffff; color: #24292f; }
.sw-stderr { background: #fff5f5; color: #9a3412; }
.sw-traceback { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }
.sw-image { max-width: 100%; border-radius: 4px; background: #fff; }
.sw-html, .sw-svg { background: #fff; border-radius: 4px; padding: 6px; overflow-x: auto; }
"""


# --------------------------------------------------------------------------- #
# The widget                                                                  #
# --------------------------------------------------------------------------- #

class ExerciseOutputWidget(anywidget.AnyWidget):
    """Everything a ``%%exercise`` cell produced, rendered in a shaded box.

    Parameters
    ----------
    result : script_widget.executor.ExerciseResult
        The outcome of ``run_exercise``. All traits below are copied from
        it once at construction time and never change afterward.

    Attributes
    ----------
    stdout, stderr : str
        Captured output streams.
    outputs : list of dict
        Captured rich display MIME bundles (see ``ExerciseResult.outputs``).
    success : bool
        Whether the cell ran without raising.
    error : str
        Short exception summary, or ``""`` if none.
    traceback : str
        Full formatted traceback, or ``""`` if none.
    """

    _esm = _ESM
    _css = _CSS

    stdout = traitlets.Unicode("").tag(sync=True)
    stderr = traitlets.Unicode("").tag(sync=True)
    outputs = traitlets.List(traitlets.Dict()).tag(sync=True)
    success = traitlets.Bool(True).tag(sync=True)
    error = traitlets.Unicode("").tag(sync=True)
    traceback = traitlets.Unicode("").tag(sync=True)

    def __init__(self, result):
        super().__init__()
        self.stdout = result.stdout
        self.stderr = result.stderr
        self.outputs = result.outputs
        self.success = result.success
        self.error = result.error or ""
        self.traceback = result.traceback or ""


def register_exercise_magic(ipython=None):
    r"""Register the `%%exercise` cell magic.

    In IPython/Jupyter, `%%exercise` runs the cell body in a fresh, isolated
    namespace (see ``executor.run_exercise``) and displays everything it
    produced -- stdout, stderr, rich output, any exception -- in a shaded
    ``ExerciseOutputWidget`` below the cell::

        %%exercise
        import matplotlib.pyplot as plt
        plt.plot([1, 2, 3])

    Nothing the cell defines or imports persists past that one run, and
    nothing the notebook has already defined is visible to it except
    through the cell's own imports.

    Called automatically on import; returns True when a live shell is
    found, False otherwise (e.g. plain Python).
    """
    ip = ipython or get_ipython()
    if ip is None:
        return False

    def exercise(line, cell):
        result = run_exercise(cell or "", ip=ip)
        _ipy_display(ExerciseOutputWidget(result))

    ip.register_magic_function(exercise, magic_kind="cell", magic_name="exercise")
    return True


try:
    register_exercise_magic()
except Exception:
    pass
