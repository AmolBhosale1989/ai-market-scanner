import math

import pandas as pd

from scanner.control_plane import _canonical, _clean, _hash


def test_signed_zero_has_one_canonical_representation():
    assert _clean(-0.0) == 0.0
    assert math.copysign(1.0, _clean(-0.0)) == 1.0
    assert _canonical({"value": -0.0}) == _canonical({"value": 0.0})
    assert _hash([{"value": -0.0}]) == _hash([{"value": 0.0}])


def test_dataframe_signed_zero_hash_matches_jsonb_equivalent():
    records = pd.DataFrame([{"ticker": "A", "change": -0.0}]).to_dict("records")
    assert _hash(records) == _hash([{"ticker": "A", "change": 0.0}])
