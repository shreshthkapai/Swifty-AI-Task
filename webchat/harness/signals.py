"""Conservative lexical signals shared by routing and tool visibility policy."""

from __future__ import annotations

import re

from .state import WorkflowDomain


DOMAIN_SIGNALS = {
    WorkflowDomain.VEHICLES: re.compile(
        r"\b(vehicle|car|cars|bmw|mini|volvo|x[1-7]|ix[1-3]?|suv|hatchback|saloon|automatic|manual|petrol|diesel|electric|hybrid|budget|cheaper|boot|mileage|stock)\b"
    ),
    WorkflowDomain.SALES: re.compile(
        r"\b(sales|enquiry|finance|callback|call me|part exchange|trade in|valuation|interest|buy|price)\b"
    ),
    WorkflowDomain.TEST_DRIVE: re.compile(r"\b(test drive|drive it|try it|slot)\b"),
    WorkflowDomain.WORKSHOP: re.compile(
        r"\b(workshop|service|servicing|mot|repair|maintenance|tyres?|booking)\b"
    ),
    WorkflowDomain.DEALERSHIP: re.compile(
        r"\b(dealer|dealership|showroom|branch|location|opening|hours|contact|manchester|liverpool|stockport|bolton)\b"
    ),
}

_OBVIOUSLY_UNRELATED = re.compile(
    r"\b(moon|planet|astronomy|world cup|football score|sports score|bake|baking|recipe|cake|celebrity gossip|horoscope)\b"
)


def signalled_domains(text: str) -> frozenset[WorkflowDomain]:
    normalized = text.casefold()
    return frozenset(
        domain for domain, pattern in DOMAIN_SIGNALS.items() if pattern.search(normalized)
    )


def is_obviously_unrelated(text: str) -> bool:
    """Return true only for explicit unrelated subjects with no dealer signal."""
    normalized = text.casefold()
    return bool(_OBVIOUSLY_UNRELATED.search(normalized)) and not signalled_domains(normalized)
