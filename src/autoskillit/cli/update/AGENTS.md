# update/

Update and upgrade machinery for the autoskillit package.

## Architecture Notes

`_update_checks.py` is the facade for the startup path; `_update.py` is the facade for the explicit `autoskillit update` command. Both reuse the same `upgrade_command()` policy from `cli/install/_install_info.py`.
