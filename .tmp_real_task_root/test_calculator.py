import pytest
from calculator import div
def test_div(): assert div(10, 2) == 5
def test_div_zero_raises():
    with pytest.raises(ValueError):
        div(1, 0)