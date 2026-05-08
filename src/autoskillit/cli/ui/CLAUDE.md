# ui/

Terminal UI primitives for the CLI layer.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Package marker (no imports) |
| `_ansi.py` | `supports_color()` — respects `NO_COLOR` and `TERM=dumb` |
| `_menu.py` | `run_selection_menu()`, `render_numbered_menu()`, `SLOT_ZERO_SELECTED` sentinel |
| `_terminal.py` | `terminal_guard()` — saves/restores TTY attributes around interactive subprocess launch |
| `_timed_input.py` | `timed_prompt()` and `status_line()` — every CLI `input()` must go through `timed_prompt()` |

## Architecture Notes

`_timed_input.py` is the lowest-level primitive; `_menu.py` depends on it. `_terminal.py` is independent. The `timed_prompt()` contract is enforced by `test_input_tty_contracts.py`.
