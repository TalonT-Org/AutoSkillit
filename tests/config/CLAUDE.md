# config/

Configuration loading, defaults, and schema tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `test_config.py` | Tests for configuration loading and resolution |
| `test_config_split.py` | Structural guard for config split |
| `test_defaults.py` | Settings coherence guard: natural_exit_grace_seconds and exit_after_stop_delay_ms coherence |
| `test_fleet_config.py` | Tests for FleetConfig loading and validation |
| `test_helpers.py` | Tests for autoskillit.config resolve_ingredient_defaults |
| `test_packs_config.py` | Tests for PacksConfig loading and validation |
| `test_providers_config.py` | Tests for ProvidersConfig loading and validation |
| `test_settings_allowed_labels.py` | Tests for GitHubConfig.allowed_labels field and check_label_allowed validation |
| `test_settings_staged_label.py` | Tests for GitHubConfig.staged_label field and config layer resolution |
| `test_skills_config.py` | Tests for SkillsConfig loading and validation |
| `test_subsets_config.py` | Tests for SubsetsConfig loading and validation |
| `test_workspace_temp_dir_config.py` | Tests for workspace.temp_dir layered config resolution |
| `test_write_config_layer.py` | Tests for write_config_layer atomic write and validation |
