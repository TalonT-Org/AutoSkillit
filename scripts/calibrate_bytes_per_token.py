"""Report a conservative bytes-per-token calibration for bundled recipe YAML."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from autoskillit.core import ASCII_YAML_POLICY, Utf8ByteLimit


def main() -> None:
    recipe_root = Path(__file__).resolve().parent.parent / "src" / "autoskillit" / "recipes"
    paths = sorted(recipe_root.glob("*.yaml")) + sorted((recipe_root / "contracts").glob("*.yaml"))
    ratios: list[Fraction] = []
    for path in paths:
        byte_count = len(path.read_bytes())
        token_count = ASCII_YAML_POLICY.to_tokens(Utf8ByteLimit(byte_count)).value
        ratios.append(Fraction(byte_count, token_count))
    if not ratios:
        raise SystemExit("no bundled recipes found")
    calibrated = sorted(ratios)[max(0, (len(ratios) - 1) // 20)]
    print(
        "5th-percentile bundled-recipe bytes/token: "
        f"{float(calibrated):.4f} ({calibrated.numerator}/{calibrated.denominator})"
    )


if __name__ == "__main__":
    main()
