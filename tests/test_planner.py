from app.fit.planner import build_occupancies


def test_family_two_plus_two_shares_one_room():
    g = build_occupancies(2, [5, 8], None)
    assert [(x.rooms, x.adults, x.children, x.child_ages) for x in g] == [(1, 2, 2, [5, 8])]


def test_four_adults_two_rooms_grouped():
    g = build_occupancies(4, [], None)
    assert [(x.rooms, x.adults) for x in g] == [(2, 2)]


def test_explicit_rooms_split_children():
    g = build_occupancies(3, [10], 2)
    assert sum(x.rooms for x in g) == 2 and sum(x.children * x.rooms for x in g) == 1
