from oa_api.routers.challenges import _estimated_minutes


def test_estimate_follows_difficulty_not_grader_runtime() -> None:
    assert [_estimated_minutes(d) for d in ("easy", "medium", "hard")] == [10, 20, 35]


def test_unknown_difficulty_gets_a_neutral_default() -> None:
    assert _estimated_minutes("nightmare") == 15
