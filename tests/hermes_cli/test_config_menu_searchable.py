"""Config-menu searchability wiring (fork enhancement).

Upstream added a ``searchable`` flag to the single-select curses pickers
(``curses_radiolist`` / ``curses_single_select``) and wired it into the model
picker. This fork additionally threads ``searchable=True`` through the
*config-menu* single-select wrappers (setup, tools, memory, plugins, mcp,
fallback) so the long provider/model/tool lists in those menus get
type-to-filter too.

These are contract tests: each single-select wrapper must forward
``searchable`` to ``curses_radiolist``. The multi-select checklists must NOT —
upstream's search driver clears the filter when search exits (so a filtered
row can never be toggled), which makes searchable multi-select unusable. The
checklist wrappers therefore stay on upstream's plain ``curses_checklist``
signature.
"""

from unittest import mock


def _capture_radiolist(return_value=1):
    """Patch curses_radiolist, returning a (patcher, captured_kwargs) pair."""
    captured = {}

    def _fake(*args, **kwargs):
        captured.update(kwargs)
        captured["_args"] = args
        return return_value

    return mock.patch("hermes_cli.curses_ui.curses_radiolist", _fake), captured


def test_setup_curses_prompt_choice_forwards_searchable():
    from hermes_cli import setup

    patcher, captured = _capture_radiolist(return_value=2)
    with patcher:
        setup._curses_prompt_choice("Q", ["a", "b", "c"], default=0, searchable=True)
    assert captured.get("searchable") is True


def test_setup_prompt_choice_forwards_searchable():
    from hermes_cli import setup

    patcher, captured = _capture_radiolist(return_value=2)
    with patcher:
        # default=0, radiolist returns 2 -> non-default branch, no input() prompt
        setup.prompt_choice("Q", ["a", "b", "c"], default=0, searchable=True)
    assert captured.get("searchable") is True


def test_setup_prompt_choice_defaults_to_not_searchable():
    from hermes_cli import setup

    patcher, captured = _capture_radiolist(return_value=2)
    with patcher:
        setup.prompt_choice("Q", ["a", "b", "c"], default=0)
    assert captured.get("searchable") is False


def test_tools_config_prompt_choice_forwards_searchable():
    from hermes_cli import tools_config

    patcher, captured = _capture_radiolist(return_value=0)
    with patcher:
        tools_config._prompt_choice("Q", ["a", "b"], default=0, searchable=True)
    assert captured.get("searchable") is True


def test_memory_setup_select_is_searchable():
    from hermes_cli import memory_setup

    patcher, captured = _capture_radiolist(return_value=0)
    with patcher:
        memory_setup._curses_select("Pick provider", [("a", "desc"), ("b", "")])
    assert captured.get("searchable") is True


def test_prompt_checklist_does_not_pass_searchable():
    """Multi-select must stay on upstream's plain checklist signature."""
    from hermes_cli import setup

    captured = {}

    def _fake_checklist(*args, **kwargs):
        captured["kwargs"] = kwargs
        return set()

    with mock.patch("hermes_cli.curses_ui.curses_checklist", _fake_checklist):
        setup.prompt_checklist("T", ["a", "b"], pre_selected=[0])
    assert "searchable" not in captured["kwargs"]
