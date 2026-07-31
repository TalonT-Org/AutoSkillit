# ui/

Terminal UI primitives for the CLI layer.

The package initializer remains import-free. Every CLI `input()` must go through
`timed_prompt()`.

## Architecture Notes

`_timed_input.py` is the lowest-level primitive; `_menu.py` depends on it. `_terminal.py` is independent. The `timed_prompt()` contract is enforced by `test_input_tty_contracts.py`.
