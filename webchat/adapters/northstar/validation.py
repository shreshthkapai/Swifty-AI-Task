"""Northstar-specific local validation matching the supplied platform contract."""

from __future__ import annotations

import re


_PHONE_PATTERN = re.compile(r"^(?:0)(?:1|7)\d{8,9}$")


def customer_phone_is_valid(value: str) -> bool:
    """Accept the UK phone formats supported by Northstar."""
    phone = re.sub(r"[\s().-]", "", value.strip())
    if phone.startswith("+44"):
        phone = f"0{phone[3:]}"
    return _PHONE_PATTERN.fullmatch(phone) is not None
