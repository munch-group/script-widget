"""Headless tests for the %%exercise execution engine (executor.run_exercise)."""
import pytest

from script_widget.executor import run_exercise


# --------------------------------------------------------------------------- #
# Without a live IPython shell -- exercises the plain stdout/stderr-only     #
# capture path (e.g. run under plain pytest, no kernel).                     #
# --------------------------------------------------------------------------- #

def test_trailing_expression_is_captured_as_a_text_output():
    result = run_exercise("a = 10\nb = 5\na + b")
    assert result.success is True
    assert result.outputs == [{"text/plain": "15"}]


def test_stdout_is_captured():
    result = run_exercise("print('hi')")
    assert result.success is True
    assert result.stdout == "hi\n"


def test_assignment_is_not_a_result():
    # No comparable "cell output" for an assignment, exactly like a real
    # notebook cell -- see puzzle_widget.checker's identical convention.
    result = run_exercise("x = 1\nx += 1")
    assert result.success is True
    assert result.outputs == []


def test_empty_cell_succeeds_with_no_output():
    result = run_exercise("")
    assert result.success is True
    assert result.stdout == result.stderr == ""
    assert result.outputs == []


def test_exception_is_caught_and_reported():
    result = run_exercise("1 / 0")
    assert result.success is False
    assert result.error == "ZeroDivisionError: division by zero"
    assert "ZeroDivisionError" in result.traceback


def test_syntax_error_is_caught_and_reported():
    result = run_exercise("if x:")
    assert result.success is False
    assert "SyntaxError" in result.error


def test_each_run_gets_a_fresh_namespace():
    # Nothing a cell defines persists to the next run -- run_exercise takes
    # no namespace parameter at all, unlike a notebook's shared `user_ns`.
    run_exercise("leaked = 123")
    result = run_exercise("leaked")
    assert result.success is False
    assert "NameError" in result.error


def test_import_inside_the_cell_returns_the_same_live_module(monkeypatch):
    # Re-importing an already-loaded module is a sys.modules cache hit -- it
    # hands back the exact same object, not a fresh reload, so mutating an
    # attribute on it through the "isolated" cell is visible outside too.
    # This is the mechanism that lets a cell "access imported variables/
    # models" (e.g. plt) without run_exercise ever seeing the notebook's
    # namespace.
    import sys
    import types

    sentinel = types.ModuleType("script_widget_test_sentinel_module")
    sentinel.value = "original"
    monkeypatch.setitem(sys.modules, "script_widget_test_sentinel_module", sentinel)

    result = run_exercise(
        "import script_widget_test_sentinel_module as m\n"
        "m.value = 'mutated'\n"
    )
    assert result.success is True
    assert sentinel.value == "mutated"


# --------------------------------------------------------------------------- #
# With a live IPython shell -- exercises capture_output + the post_execute   #
# matplotlib-flush path, which only exist with a real shell present.         #
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def ip():
    from IPython.testing.globalipapp import get_ipython

    shell = get_ipython()
    shell.run_line_magic("matplotlib", "inline")
    # globalipapp's TerminalInteractiveShell restricts rich display to
    # text/plain by default (sensible for a real terminal, not a Jupyter
    # kernel) -- widen it so this test shell behaves like a real kernel.
    shell.display_formatter.active_types = shell.display_formatter.format_types
    return shell


def test_trailing_expression_is_displayed_via_ipython(ip):
    result = run_exercise("a = 10\na", ip=ip)
    assert result.success is True
    assert result.outputs == [{"text/plain": "10"}]


def test_explicit_display_call_is_captured(ip):
    result = run_exercise(
        "from IPython.display import display\n"
        "display({'text/plain': 'explicit'}, raw=True)\n",
        ip=ip,
    )
    assert {"text/plain": "explicit"} in result.outputs


def test_matplotlib_figure_is_flushed_after_the_cell(ip):
    # No plt.show() needed -- post_execute's flush_figures hook (registered
    # by %matplotlib inline) picks up any still-open figure after the cell
    # finishes, exactly like it would after a normal cell.
    source = "import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])\n"
    result = run_exercise(source, ip=ip)
    assert result.success is True
    assert any("image/png" in o for o in result.outputs)


def test_figure_is_flushed_even_if_the_cell_raises_after_drawing(ip):
    source = "import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])\nraise ValueError('boom')\n"
    result = run_exercise(source, ip=ip)
    assert result.success is False
    assert "ValueError" in result.error
    assert any("image/png" in o for o in result.outputs)
