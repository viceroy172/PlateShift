# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a print costs in filament, from the grams the slicer reports per filament."""

from __future__ import annotations

import locale
import subprocess
import sys

FALLBACK_CURRENCY = "$"


def regional_currency() -> str:
    """the currency symbol from the computer's regional settings, else $"""
    symbol = ""
    if sys.platform == "win32":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(16)
            # LOCALE_SCURRENCY of the user's default locale
            if ctypes.windll.kernel32.GetLocaleInfoEx(None, 0x14, buf, len(buf)):
                symbol = buf.value
        except (AttributeError, OSError):
            pass
    if not symbol:
        name = ""
        if sys.platform == "darwin":
            # apps started from the finder get no LANG, so ask for the region itself
            try:
                found = subprocess.run(["defaults", "read", "-g", "AppleLocale"], capture_output=True,
                                       text=True, timeout=5).stdout.strip()
                name = found.split("@")[0] + ".UTF-8" if found else ""
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            old = locale.setlocale(locale.LC_MONETARY)
            try:
                locale.setlocale(locale.LC_MONETARY, name)
                symbol = locale.localeconv().get("currency_symbol", "")
            finally:
                locale.setlocale(locale.LC_MONETARY, old)
        except (locale.Error, ValueError):
            pass
    return symbol.strip() or FALLBACK_CURRENCY


def parse_price(text: str) -> float | None:
    """"350", "349,99", "R 350" -> a price; None for empty or nonsense"""
    cleaned = "".join(c for c in str(text) if c.isdigit() or c in ".,").replace(",", ".")
    if cleaned.count(".") > 1:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value >= 0 else None


def money(amount: float, currency: str) -> str:
    """R 12.50, $12.50: letters get a space, signs don't"""
    gap = " " if currency[-1:].isalpha() else ""
    return f"{currency}{gap}{amount:,.2f}"


def cost_lines(grams_each: list[float], slots: list, prices: dict[int, float], currency: str,
               slot_word: str = "Toolhead", hint: str = "set one with the ⚙ beside its filament") -> list[str]:
    """the filament cost, one line per used slot and a total. slots: the report's
    SlotMappings (slot number + target preset). prices: slot -> price per kg"""
    by_slot = {s.slot: s.target for s in slots}
    lines, total, missing = [], 0.0, []
    for index, grams in enumerate(grams_each, start=1):
        if grams <= 0:
            continue
        price = prices.get(index) or 0.0
        if price <= 0:
            missing.append(f"{slot_word.lower()} {index} ({by_slot.get(index) or 'unknown filament'})")
            continue
        cost = grams / 1000 * price
        total += cost
        lines.append(f"{slot_word} {index}: {grams:.1f} g at {money(price, currency)}/kg = {money(cost, currency)}")
    out = []
    if lines:
        out.append(f"Filament cost: {money(total, currency)}" + (" so far" if missing else ""))
        out += [f"  {line}" for line in lines]
    if missing:
        out.append(("  " if lines else "") + "No price for " + ", ".join(missing) + f"; {hint}")
    return out
