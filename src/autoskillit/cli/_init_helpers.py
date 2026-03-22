"""Init command helpers: interactive prompts, config YAML generation, and workspace marker."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from autoskillit.config import write_config_layer
from autoskillit.core import YAMLError, atomic_write, dump_yaml_str, load_yaml
from autoskillit.recipe import list_recipes


class _ScanResult(NamedTuple):
    passed: bool
    bypass_accepted: bool = False


def _colors() -> tuple[str, str, str, str, str, str]:
    """Return (Bold, Cyan, Dim, Green, Yellow, Reset) respecting NO_COLOR."""
    from autoskillit.cli._ansi import supports_color

    c = supports_color()
    return (
        "\x1b[1m" if c else "",  # _B
        "\x1b[96m" if c else "",  # _C
        "\x1b[2m" if c else "",  # _D
        "\x1b[32m" if c else "",  # _G
        "\x1b[33m" if c else "",  # _Y
        "\x1b[0m" if c else "",  # _R
    )


_KNOWN_SCANNERS: frozenset[str] = frozenset(
    {"gitleaks", "detect-secrets", "trufflehog", "git-secrets"}
)

_SECRET_SCAN_BYPASS_PHRASE = "I accept the risk of leaking secrets without pre-commit scanning"


_MARKER_CONTENT = """\
# autoskillit workspace - do not delete
# This file authorizes reset_test_dir and reset_workspace to clear this directory.
# Created: {timestamp}
# Tool: autoskillit {version}
"""


def _require_interactive_stdin(command_name: str) -> None:
    """Pre-condition guard for any function that calls input().

    Raises SystemExit(1) with a clear message if stdin is not a TTY. Every
    prompt function in the CLI must call this before any input() invocation to
    prevent silent EOFError crashes in non-interactive environments (CI, scripts,
    piped invocations).

    Parameters
    ----------
    command_name
        Human-readable name of the command requiring interactivity, e.g.
        "autoskillit init" or "autoskillit order". Included in the error message.
    """
    if not sys.stdin.isatty():
        print(
            f"\n  ERROR: '{command_name}' requires an interactive terminal.\n"
            f"  Run with the appropriate flag to provide this value non-interactively,\n"
            f"  or run in an interactive shell.\n"
        )
        raise SystemExit(1)


def _prompt_recipe_choice() -> str:
    _require_interactive_stdin("autoskillit order")
    available = list_recipes(Path.cwd()).items
    if not available:
        print("No recipes found. Run 'autoskillit recipes list' to check.")
        raise SystemExit(1)
    print("Available recipes:")
    for i, r in enumerate(available, 1):
        print(f"  {i}. {r.name}")
    return input("Recipe name: ").strip()


def _prompt_test_command() -> list[str]:
    _require_interactive_stdin("autoskillit init")
    default = "task test-all"
    answer = input(f"Test command [{default}]: ").strip()
    return (answer if answer else default).split()


def _detect_github_repo() -> str | None:
    """Try to detect owner/repo from the git remote URL."""
    from autoskillit.core import parse_github_repo

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return parse_github_repo(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _prompt_github_repo() -> str | None:
    """Prompt the user for their GitHub repository, auto-detecting from git remote."""
    from autoskillit.core import parse_github_repo

    _B, _C, _D, _G, _Y, _R = _colors()
    detected = _detect_github_repo()

    if detected:
        print(f"\n  {_Y}GitHub repo{_R}  {_G}{detected}{_R} {_D}(detected from git remote){_R}")
        value = input(f"  {_D}Press Enter to confirm, or type a different repo:{_R} ").strip()
    else:
        print(f"\n  {_Y}GitHub repo{_R}  {_D}owner/repo, URL, or blank to skip{_R}")
        value = input(f"  {_D}Repository:{_R} ").strip()

    if not value:
        return detected

    # Accept full URLs — parse_github_repo normalises them to owner/repo
    parsed = parse_github_repo(value)
    if parsed:
        return parsed

    # Accept bare owner/repo if it looks valid
    if "/" in value and not value.startswith("http"):
        return value

    print(f"  {_Y}Warning:{_R} '{value}' doesn't look like owner/repo — using as-is.")
    return value


def _is_gh_authenticated() -> bool:
    """Return True if the gh CLI is authenticated."""
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _create_secrets_template(project_dir: Path) -> None:
    """Create .autoskillit/.secrets.yaml with a placeholder for github.token."""
    autoskillit_dir = project_dir / ".autoskillit"
    autoskillit_dir.mkdir(exist_ok=True)
    secrets_path = autoskillit_dir / ".secrets.yaml"
    if secrets_path.exists():
        return  # Never overwrite existing secrets
    atomic_write(
        secrets_path,
        "# AutoSkillit secrets — never commit this file\n"
        "# This file is gitignored — do not commit it\n\n"
        "# GitHub authentication (choose one):\n"
        "#   Option 1 (recommended): Run 'gh auth login' — the gh CLI handles auth\n"
        "#     for all MCP tool commands (issues, PRs, CI status).\n"
        "#   Option 2: Set a token below — used by background watchers (CI, merge queue)\n"
        "#     that poll the GitHub API directly via httpx.\n"
        "#   If gh is authenticated, the token below is optional.\n"
        "github:\n"
        "  token: ''  # Optional — only needed if gh auth is unavailable\n",
    )


def _detect_secret_scanner(project_dir: Path) -> bool:
    """Return True if .pre-commit-config.yaml references a known secret scanner."""
    config_path = project_dir / ".pre-commit-config.yaml"
    if not config_path.is_file():
        return False
    try:
        data = load_yaml(config_path) or {}
    except YAMLError:
        return False
    if not isinstance(data, dict):
        return False
    repos = data.get("repos", [])
    if not isinstance(repos, list):
        return False
    for repo in repos:
        for hook in repo.get("hooks", []):
            if hook.get("id") in _KNOWN_SCANNERS:
                return True
    return False


def _log_secret_scan_bypass(project_dir: Path) -> None:
    """Persist bypass acceptance timestamp to .autoskillit/.state.yaml.

    .state.yaml holds internal operational state and is never schema-validated.
    Writing to config.yaml would inject an unknown key into the schema-validated
    layer, causing ConfigSchemaError on every subsequent load_config call.
    """
    state_path = project_dir / ".autoskillit" / ".state.yaml"
    try:
        raw = (load_yaml(state_path) or {}) if state_path.is_file() else {}
        data: dict = raw if isinstance(raw, dict) else {}
    except YAMLError:
        data = {}
    data.setdefault("safety", {})["secret_scan_bypass_accepted"] = datetime.now(UTC).isoformat()
    state_path.parent.mkdir(exist_ok=True)
    atomic_write(state_path, dump_yaml_str(data, default_flow_style=False, allow_unicode=True))


def _check_secret_scanning(project_dir: Path) -> _ScanResult:
    """Gate: require secret scanning hook or explicit typed consent.

    Returns _ScanResult(passed=True) if scanner found.
    Returns _ScanResult(passed=True, bypass_accepted=True) if user accepted bypass phrase.
    Returns _ScanResult(passed=False) if the check fails and init should abort.
    The caller is responsible for calling _log_secret_scan_bypass when bypass_accepted=True.
    """
    _B, _C, _D, _G, _Y, _R = _colors()

    if _detect_secret_scanner(project_dir):
        print(f"  {_Y}{'secret scanning':>12}{_R}  {_G}✓ hook detected{_R}")
        return _ScanResult(True)

    # No scanner found — require explicit opt-in
    if not sys.stdin.isatty():
        print(
            f"\n  {_B}ERROR:{_R} No secret scanning hook found in .pre-commit-config.yaml.\n"
            f"  AutoSkillit commits code automatically. Without a secret scanner,\n"
            f"  leaked credentials are inevitable.\n\n"
            f"  Add gitleaks, detect-secrets, trufflehog, or git-secrets to\n"
            f"  .pre-commit-config.yaml before running 'autoskillit init'.\n"
            f"  Non-interactive mode cannot bypass this check.\n"
        )
        return _ScanResult(False)

    # Interactive: show warning and require consent phrase
    border = "━" * 62
    print(f"\n  {_Y}{border}{_R}")
    print(f"  {_Y}  WARNING: No secret scanning hook detected{_R}")
    print(f"  {_Y}{border}{_R}")
    print(
        "  AutoSkillit automates code commits at scale. Without a\n"
        "  secret scanner in your pre-commit pipeline, leaked API\n"
        "  keys and credentials are not a matter of if, but when.\n\n"
        "  Recommended: add gitleaks to .pre-commit-config.yaml\n"
        "  before proceeding.\n"
    )
    print("  To bypass, type exactly:\n")
    print(f"  {_D}{_SECRET_SCAN_BYPASS_PHRASE}{_R}\n")
    response = input("  > ").strip()
    if response != _SECRET_SCAN_BYPASS_PHRASE:
        print(f"\n  {_B}Aborted.{_R} Phrase did not match.")
        return _ScanResult(False)

    # Bypass accepted — caller logs regardless of whether a new config was written
    print(f"  {_Y}{'bypass':>12}{_R}  {_D}accepted — logged to config.yaml{_R}")
    return _ScanResult(True, bypass_accepted=True)


def _is_plugin_installed() -> bool:
    """Return True if autoskillit is installed as a Claude plugin.

    Returns False when claude CLI is not on PATH, times out, or is otherwise unavailable.
    """
    try:
        result = subprocess.run(
            ["claude", "plugin", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0 and "autoskillit" in result.stdout
    except FileNotFoundError:
        return False  # claude CLI not on PATH
    except (subprocess.TimeoutExpired, OSError):
        return False  # CLI unavailable or timed out


def _generate_config_yaml(test_command: list[str]) -> str:
    """Generate config YAML with active settings and commented advanced sections."""
    cmd_str = json.dumps(test_command)
    return f"""\
test_check:
  command: {cmd_str}
  # timeout: 600

safety:
  reset_guard_marker: ".autoskillit-workspace"
  require_dry_walkthrough: true
  test_gate_on_merge: true

# --- Advanced settings (uncomment and configure as needed) ---
#
# classify_fix:
#   path_prefixes: []
#
# reset_workspace:
#   command: null
#   preserve_dirs: []
#
# implement_gate:
#   marker: "Dry-walkthrough verified = TRUE"
#   skill_names: ["/autoskillit:implement-worktree", "/autoskillit:implement-worktree-no-merge"]
#
# run_skill:
#   timeout: 7200
#   stale_threshold: 1200
#   completion_marker: "%%ORDER_UP%%"
"""


def _user_claude_json_path() -> Path:
    """Return path to ~/.claude.json (user-scoped MCP server config)."""
    return Path.home() / ".claude.json"


def _register_mcp_server(claude_json_path: Path) -> None:
    """Write autoskillit MCP server entry to claude.json (idempotent)."""
    data: dict = {}
    if claude_json_path.exists():
        try:
            data = json.loads(claude_json_path.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{claude_json_path} contains invalid JSON. "
                f"Fix or remove it before running 'autoskillit init'. Error: {exc}"
            ) from exc
        except OSError as exc:
            raise OSError(f"{claude_json_path} could not be read: {exc}") from exc
    data.setdefault("mcpServers", {})
    data["mcpServers"]["autoskillit"] = {
        "type": "stdio",
        "command": "autoskillit",
        "args": [],
    }
    atomic_write(claude_json_path, json.dumps(data, indent=2))


def _print_next_steps(*, context: str = "install") -> None:
    _B, _C, _D, _G, _Y, _R = _colors()
    if context == "install":
        steps = [
            ("autoskillit init", "create project config"),
            ("autoskillit doctor", "verify your setup"),
        ]
    else:
        steps = [
            ("autoskillit cook setup-project", "generate tailored recipes"),
            ("autoskillit order <recipe>", "run a recipe pipeline"),
            ("autoskillit cook", "interactive session"),
            ("autoskillit doctor", "verify setup"),
        ]
    print(f"  {_B}Next steps:{_R}")
    for i, (cmd, desc) in enumerate(steps, 1):
        print(f"  {_D}{i}.{_R} {_G}{cmd}{_R}  {_D}{desc}{_R}")


def _register_all(scope: str, project_dir: Path) -> None:
    """Ensure project temp dir, register hooks and MCP server, print summary."""
    from autoskillit.cli._hooks import (
        _claude_settings_path,
        _evict_stale_autoskillit_hooks,
        sync_hooks_to_settings,
    )
    from autoskillit.core import ensure_project_temp

    _B, _C, _D, _G, _Y, _R = _colors()

    ensure_project_temp(project_dir)
    settings_path = _claude_settings_path(scope)
    _evict_stale_autoskillit_hooks(settings_path)
    sync_hooks_to_settings(settings_path)

    # Prompt for github.default_repo if running interactively
    github_repo = None
    if sys.stdin.isatty():
        github_repo = _prompt_github_repo()
        if github_repo:
            config_path = project_dir / ".autoskillit" / "config.yaml"
            if config_path.exists():
                try:
                    config_data = load_yaml(config_path) or {}
                    if not config_data.get("github", {}).get("default_repo"):
                        config_data.setdefault("github", {})["default_repo"] = github_repo
                        write_config_layer(config_path, config_data)
                except (OSError, YAMLError) as exc:
                    print(f"  {_Y}Warning:{_R} could not write github.default_repo: {exc}")
            else:
                try:
                    autoskillit_dir = project_dir / ".autoskillit"
                    autoskillit_dir.mkdir(exist_ok=True)
                    write_config_layer(config_path, {"github": {"default_repo": github_repo}})
                except (OSError, YAMLError) as exc:
                    print(f"  {_Y}Warning:{_R} could not write github.default_repo: {exc}")

    _create_secrets_template(project_dir)

    plugin_ok = _is_plugin_installed()
    if not plugin_ok:
        _register_mcp_server(_user_claude_json_path())

    # --- Summary block ---
    print()
    from autoskillit import __version__

    print(f"  {_B}{_C}AUTOSKILLIT {__version__}{_R}  {_D}Project initialized.{_R}")
    print()
    print(f"  {_Y}{'config':>12}{_R}  {_G}{project_dir / '.autoskillit' / 'config.yaml'}{_R}")
    if github_repo:
        print(f"  {_Y}{'github':>12}{_R}  {_G}{github_repo}{_R}")
    gh_ok = _is_gh_authenticated()
    if gh_ok:
        print(f"  {_Y}{'gh auth':>12}{_R}  {_G}authenticated{_R}")
    else:
        print(f"  {_Y}{'gh auth':>12}{_R}  {_D}not found — run{_R} {_G}gh auth login{_R}")
    if plugin_ok:
        print(f"  {_Y}{'plugin':>12}{_R}  {_G}registered{_R}")
    else:
        print(f"  {_Y}{'plugin':>12}{_R}  {_G}registered via ~/.claude.json{_R}")
    print(f"  {_Y}{'hooks':>12}{_R}  {_G}synced{_R} {_D}({scope} scope){_R}")
    print()
    _print_next_steps(context="init")
