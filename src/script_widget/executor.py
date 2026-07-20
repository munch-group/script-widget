"""script_widget.executor
=========================

The engine behind the ``%%exercise`` widget: run a cell body in a namespace
that is fresh on every call and discarded afterward -- nothing a cell
defines or imports persists past that one run, and nothing the notebook
has already defined is visible to it except through the cell's own
``import``/``from ... import`` statements. Because Python caches loaded
modules in ``sys.modules``, such an import is essentially free and hands
back the exact same live object the notebook already has (the same
``plt``, with the same open figures; the same model, with the same
weights) -- nothing is actually reloaded or reinitialized.

``run_exercise`` never raises: a ``SyntaxError`` from the cell, or any
exception the cell's own code raises, is caught and reported via
``ExerciseResult.error``/``.traceback`` instead.
"""

from __future__ import annotations

import ast
import contextlib
import io
import traceback

__all__ = ["ExerciseResult", "run_exercise"]


class ExerciseResult:
    """The outcome of running one ``%%exercise`` cell.

    Attributes
    ----------
    stdout : str
        Everything the cell printed to stdout.
    stderr : str
        Everything the cell printed to stderr.
    outputs : list of dict
        Rich display MIME bundles produced by the cell -- a trailing bare
        expression's repr, an explicit ``display(...)`` call, or (when run
        with a live IPython shell) a matplotlib figure flushed after
        execution. Each entry maps MIME type to its data, e.g.
        ``{"image/png": "<base64>", "text/plain": "..."}``.
    error : str or None
        A short ``"ExceptionType: message"`` summary if the cell failed to
        parse or raised while running, else ``None``.
    traceback : str or None
        The full formatted traceback for ``error``, else ``None``.
    """

    __slots__ = ("stdout", "stderr", "outputs", "error", "traceback")

    def __init__(self, stdout="", stderr="", outputs=None, error=None, traceback=None):
        self.stdout = stdout
        self.stderr = stderr
        self.outputs = outputs or []
        self.error = error
        self.traceback = traceback

    @property
    def success(self):
        return self.error is None

    def __repr__(self):
        return (
            f"ExerciseResult(success={self.success!r}, error={self.error!r}, "
            f"outputs={len(self.outputs)})"
        )


def _last_line_value(tree, namespace):
    """Run every statement in ``tree`` except the last in ``namespace``,
    then return the value associated with the last one -- mirroring how a
    Jupyter cell only ever shows an *output* for a bare trailing
    expression, never for a statement like an assignment (see
    ``puzzle_widget.checker``'s identical helper for the same rationale):

    - if it's a bare expression (``ast.Expr``), the expression's value;
    - otherwise (e.g. an assignment), the line still runs -- so a runtime
      error there is still caught by the caller -- but there is no
      comparable "cell output", so this returns ``None``.
    """
    if not tree.body:
        return None
    *body, last = tree.body
    if body:
        exec(compile(ast.Module(body=body, type_ignores=[]), "<exercise>", "exec"), namespace)
    if isinstance(last, ast.Expr):
        return eval(compile(ast.Expression(body=last.value), "<exercise>", "eval"), namespace)
    exec(compile(ast.Module(body=[last], type_ignores=[]), "<exercise>", "exec"), namespace)
    return None


def run_exercise(source, ip=None):
    """Run ``source`` in a fresh, isolated namespace and capture everything
    it produced.

    Parameters
    ----------
    source : str
        The cell body.
    ip : InteractiveShell or None
        The live IPython shell, if any. When given, stdout/stderr and rich
        display output (``display(...)`` calls, a trailing bare
        expression's repr, matplotlib figures) are captured via
        ``IPython.utils.capture.capture_output``, and the shell's
        ``post_execute`` event is triggered after running so any
        matplotlib-inline flush hook fires exactly as it would after a
        normal cell. Without ``ip`` (e.g. under plain ``pytest``), only
        stdout/stderr are captured and the trailing value (if any) is
        reported as a ``text/plain`` output.

    Returns
    -------
    ExerciseResult
    """
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as e:
        return ExerciseResult(error=f"SyntaxError: {e.msg}", traceback=traceback.format_exc())

    namespace = {}
    if ip is None:
        return _run_without_ipython(tree, namespace)
    return _run_with_ipython(tree, namespace, ip)


def _run_without_ipython(tree, namespace):
    stdout, stderr = io.StringIO(), io.StringIO()
    outputs = []
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            value = _last_line_value(tree, namespace)
        if value is not None:
            outputs.append({"text/plain": repr(value)})
    except Exception as e:
        return ExerciseResult(
            stdout=stdout.getvalue(), stderr=stderr.getvalue(), outputs=outputs,
            error=f"{type(e).__name__}: {e}", traceback=traceback.format_exc(),
        )
    return ExerciseResult(stdout=stdout.getvalue(), stderr=stderr.getvalue(), outputs=outputs)


def _run_with_ipython(tree, namespace, ip):
    from IPython.display import display as ipy_display
    from IPython.utils.capture import capture_output

    error = tb = None
    with capture_output() as cap:
        try:
            value = _last_line_value(tree, namespace)
            if value is not None:
                ipy_display(value)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            tb = traceback.format_exc()
        finally:
            # Flush any matplotlib figures the cell drew, exactly like a
            # normal cell would -- runs even if the cell raised, so a
            # figure drawn before the error still shows up.
            ip.events.trigger("post_execute")

    return ExerciseResult(
        stdout=cap.stdout, stderr=cap.stderr,
        outputs=[o.data for o in cap.outputs],
        error=error, traceback=tb,
    )
