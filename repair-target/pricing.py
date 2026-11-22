"""Pricing functions for the deliberately vulnerable repair target."""

def calculate_discount(price: float, divisor: float) -> float:
    """Return the discount amount.

    Phase 2 deliberately begins with no zero-divisor guard so the agent can
    diagnose and repair a genuine source defect.
    """
    return price / divisor

