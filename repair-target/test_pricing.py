import pytest
from pricing import calculate_discount

def test_calculate_discount():
    assert calculate_discount(100, 10) == 10

def test_zero_divisor_is_controlled():
    with pytest.raises(ValueError, match="divisor"):
        calculate_discount(100, 0)

