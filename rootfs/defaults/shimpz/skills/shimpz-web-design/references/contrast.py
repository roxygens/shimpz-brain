#!/usr/bin/env python3
"""WCAG contrast checker for the shimpz-web-design skill.

Usage:
    python3 contrast.py "#0b1020" "#e8ecf7"

Prints the contrast ratio between two colours and whether it passes WCAG AA/AAA for normal and
large text — so a palette choice is verified, not guessed. Fail-fast: a malformed colour raises
loudly (a silently-wrong ratio would green-light an inaccessible design).
"""

import sys


def _srgb_to_linear(channel: float) -> float:
    """One sRGB channel (0–1) to linear-light, per WCAG 2.x relative-luminance definition."""
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """Relative luminance (0=black … 1=white) of a #rgb or #rrggbb colour."""
    h = hex_color.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"not a hex colour: {hex_color!r} (want #rgb or #rrggbb)")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * _srgb_to_linear(r) + 0.7152 * _srgb_to_linear(g) + 0.0722 * _srgb_to_linear(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two colours (1.0 … 21.0), order-independent."""
    lum = sorted((relative_luminance(fg), relative_luminance(bg)))
    return (lum[1] + 0.05) / (lum[0] + 0.05)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write('usage: contrast.py "#fg" "#bg"\n')
        return 64
    ratio = contrast_ratio(argv[0], argv[1])
    # WCAG 2.x thresholds: (label, minimum ratio)
    thresholds = (
        ("AA  normal text", 4.5),
        ("AA  large text ", 3.0),
        ("AAA normal text", 7.0),
        ("AAA large text ", 4.5),
    )
    print(f"contrast {argv[0]} on {argv[1]}: {ratio:.2f}:1")
    for label, minimum in thresholds:
        print(f"  {label}  {'✓ pass' if ratio >= minimum else '✗ FAIL'}  (needs {minimum}:1)")
    # exit non-zero if it fails the baseline (AA normal) — a script/CI can gate on it
    return 0 if ratio >= 4.5 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
