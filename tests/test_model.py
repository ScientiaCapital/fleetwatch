from fleetwatch.model import Priority


def test_priority_rank_lists_fix_first_then_fix_soon_then_when_convenient():
    assert [p.rank for p in (Priority.FIX_FIRST, Priority.FIX_SOON, Priority.WHEN_CONVENIENT)] == [0, 1, 2]
    shuffled = [Priority.WHEN_CONVENIENT, Priority.FIX_FIRST, Priority.FIX_SOON]
    assert sorted(shuffled, key=lambda p: p.rank) == list(Priority)
