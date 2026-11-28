import pytest

from quartermaster import planner
from quartermaster.nodes import command

BOTH = ("wifi24", "wifi5", "ble")


def ids(count):
    return ["%012x" % (0xa0b1c2d3e400 + i) for i in range(count)]


def nodes(count, caps=BOTH):
    return [(node_id, caps) for node_id in ids(count)]


def test_every_country_has_its_own_channels_and_none_has_channel_14():
    assert set(planner.COUNTRIES) == {
        "01", "AT", "AU", "BE", "BG", "BR", "CA", "CH", "CN", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GB",
        "GR", "HK", "HR", "HU", "IE", "IN", "IS", "IT", "JP", "KR", "LI", "LT", "LU", "LV", "MT", "MX", "NL", "NO",
        "NZ", "PL", "PT", "RO", "SE", "SI", "SK", "TW", "US"}
    for country in planner.COUNTRIES:
        found = planner.channels(country)
        assert ("2.4", 14) not in found and len(found) == len(set(found)) >= 15
    us = planner.channels("US")
    assert [ch for band, ch in us if band == "2.4"] == list(range(1, 12))
    assert [ch for band, ch in us if band == "5"] == [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120,
                                                      124, 128, 132, 136, 140, 144, 149, 153, 157, 161, 165, 173, 177]
    assert [ch for band, ch in us if planner.passive(band, ch)] == list(range(52, 65, 4)) + list(range(100, 145, 4)) \
        + [173, 177]
    assert planner.channels("01") == [("2.4", ch) for ch in range(1, 12)] + [("5", ch) for ch in range(36, 65, 4)]
    assert ("2.4", 13) in planner.channels("JP") and not any(ch >= 149 for _, ch in planner.channels("JP"))
    assert [ch for _, ch in planner.channels("GB")][-1] == 165 and ("5", 173) in planner.channels("DE")
    assert ("5", 120) not in planner.channels("AU") and ("5", 173) in planner.channels("AU")
    assert planner.channels("CA")[-1] == ("5", 177) and planner.channels("FR") == planner.channels("DE")


def test_one_node_scans_everything_the_country_allows():
    plans = planner.make("US", [(ids(1)[0], BOTH, False)])
    plan = plans[ids(1)[0]]
    assert plan == planner.Plan(1, "US", 0xFFE, 0x1BFFFFFE, 30, 80, 110, False)
    assert planner.share_of(plan) == planner.channels("US")


@pytest.mark.parametrize("country", planner.COUNTRIES)
def test_the_nodes_share_every_channel_and_each_sweeps_in_about_the_same_time(country):
    for count in range(1, 9):
        shares = planner.split(country, nodes(count))
        assert sorted(shares) == ids(count)
        heard = [item for share in shares.values() for item in share]
        assert set(heard) == set(planner.channels(country))
        loads = [planner.sweep_ms(share) for share in shares.values()]
        assert max(loads) <= 1.2 * sum(loads) / count, (count, loads)
        assert all(len(share) == len(set(share)) for share in shares.values())


def test_channels_1_6_and_11_go_to_two_different_nodes_from_three_nodes_on():
    for count in range(1, 9):
        for country in ("US", "DE", "01", "JP"):
            heard = [item for share in planner.split(country, nodes(count)).values() for item in share]
            for band, ch in planner.channels(country):
                twice = count >= 3 and (band, ch) in (("2.4", 1), ("2.4", 6), ("2.4", 11))
                assert heard.count((band, ch)) == (2 if twice else 1), (country, count, ch)


def test_each_node_keeps_to_one_band_where_it_can():
    for country in planner.COUNTRIES:
        for count in range(2, 9):
            shares = planner.split(country, nodes(count)).values()
            assert sum(len({band for band, _ in share}) > 1 for share in shares) <= 2
            assert max(len({band for band, _ in share}) for share in shares) <= 2


def test_nodes_that_scan_one_band_only_get_channels_of_that_band():
    shares = planner.split("US", [("a" * 12, ("wifi24",)), ("b" * 12, ("wifi5",)), ("c" * 12, BOTH)])
    assert {band for band, _ in shares["a" * 12]} == {"2.4"} and {band for band, _ in shares["b" * 12]} == {"5"}
    heard = [item for share in shares.values() for item in share]
    assert set(heard) == set(planner.channels("US"))
    assert all(heard.count(("2.4", ch)) == 2 for ch in (1, 6, 11))
    assert all(len(share) == len(set(share)) for share in shares.values())


def test_a_plan_again_with_nothing_changed_keeps_every_plan_and_version():
    members = [(node_id, BOTH, False) for node_id in ids(3)]
    first = planner.make("US", members, version=4)
    again = planner.make("US", members, first, version=5)
    assert again == first and {plan.version for plan in again.values()} == {4}


def test_only_the_node_whose_plan_changed_gets_a_new_version():
    members = [(node_id, BOTH, False) for node_id in ids(3)]
    first = planner.make("US", members, version=1)
    members[1] = (members[1][0], BOTH, True)
    second = planner.make("US", members, first, version=2)
    assert [second[node_id].version for node_id in ids(3)] == [1, 2, 1] and second[ids(3)[1]].ble
    joined = planner.make("US", members + [(ids(4)[3], BOTH, False)], second, version=3)
    assert {plan.version for plan in joined.values()} == {3}
    moved = planner.make("DE", members, second, version=4)
    assert {plan.country for plan in moved.values()} == {"DE"} and {plan.version for plan in moved.values()} == {4}


def test_bluetooth_is_asked_for_only_from_a_node_that_can_do_it():
    plans = planner.make("US", [("a" * 12, ("wifi24", "wifi5"), True), ("b" * 12, BOTH, True)])
    assert not plans["a" * 12].ble and plans["b" * 12].ble


def test_an_unknown_country_falls_back_to_the_worldwide_channels():
    plan = planner.make("ZZ", [("a" * 12, BOTH, False)])["a" * 12]
    assert plan.country == "01" and planner.share_of(plan) == planner.channels("01")


def test_the_plan_command_is_the_one_the_firmware_reads():
    plan = planner.Plan(3, "US", 0x842, 0x1E, 30, 80, 110, False)
    assert command("PLAN", *planner.arguments(plan)) == b"#PLAN,3,US,0842,1e,30,80,110,0*77\n"
    assert planner.bitmaps([("2.4", 1), ("2.4", 6), ("2.4", 11), ("5", 36), ("5", 40), ("5", 44), ("5", 48)]) == \
        (0x842, 0x1E)
    assert planner.bitmaps([("5", 177)]) == (1, 1 << 28) and planner.bitmaps([("2.4", 13)]) == (1 << 13, 1)
    assert planner.share_of(planner.Plan(1, "US", 1, 1 << 28, 30, 80, 110, False)) == [("5", 177)]


def test_channels_12_and_13_are_listened_to_without_asking():
    found = planner.channels("DE")
    assert [ch for band, ch in found if band == "2.4" and planner.passive(band, ch)] == [12, 13]
    assert planner.sweep_ms(found[:13]) == 11 * planner.ACTIVE_MAX + 2 * planner.PASSIVE


def test_of_two_equally_quick_shares_the_one_with_fewer_band_switches_is_taken():
    for country, count, most in (("01", 7, 0), ("BR", 7, 1), ("CN", 8, 1), ("JP", 7, 1), ("KR", 7, 1)):
        shares = planner.split(country, nodes(count)).values()
        assert sum(len({band for band, _ in share}) > 1 for share in shares) == most, country


def test_only_the_bands_kept_are_shared():
    shares = planner.split("US", nodes(3), bands={"5"})
    heard = sorted(item for share in shares.values() for item in share)
    assert heard == [item for item in planner.channels("US") if item[0] == "5"]
    assert planner.split("01", nodes(8), bands={"5"}) and all(len(s) == 1 for s in
                                                              planner.split("01", nodes(8), bands={"5"}).values())
    plans = planner.make("US", [(node_id, BOTH, True) for node_id in ids(2)], bands=set())
    assert {(plan.ghz2, plan.ghz5, plan.ble) for plan in plans.values()} == {(1, 1, True)}
    assert planner.split("US", nodes(4), bands={"2.4"})[ids(4)[3]]


@pytest.mark.parametrize("country", ("US", "DE", "JP", "01"))
def test_a_wireless_node_gets_a_share_it_sweeps_as_soon_as_the_others_with_its_handovers(country):
    for count in range(2, 9):
        for caps in (BOTH, ("wifi24", "wifi5"), ("wifi24",)):
            group = [(node_id, caps if i % 2 else BOTH) for i, node_id in enumerate(ids(count))]
            slow = {ids(count)[0]}
            shares = planner.split(country, group, slow=slow)
            heard = {item for share in shares.values() for item in share}
            assert heard == set(planner.channels(country))
            times = {node_id: planner.sweep_ms(share) * (planner.AIR_SLOWER if node_id in slow else 1)
                     for node_id, share in shares.items()}
            even = planner.split(country, group)
            assert max(times.values()) <= max(planner.sweep_ms(share) for share in even.values()) * planner.AIR_SLOWER
            if caps == BOTH:
                assert planner.sweep_ms(shares[ids(count)[0]]) <= planner.sweep_ms(even[ids(count)[0]])
    group = nodes(3)
    assert planner.split("US", group, slow={node_id for node_id, _ in group}) == planner.split("US", group)
    two = planner.split("US", nodes(2), slow={ids(2)[0]})
    assert planner.sweep_ms(two[ids(2)[0]]) < planner.sweep_ms(two[ids(2)[1]])
    plans = planner.make("US", [(node_id, BOTH, False) for node_id in ids(2)], slow={ids(2)[1]})
    assert planner.sweep_ms(planner.share_of(plans[ids(2)[1]])) < planner.sweep_ms(planner.share_of(plans[ids(2)[0]]))


def test_a_wireless_node_is_counted_slower_where_bands_differ_and_where_1_6_and_11_are_given_twice():
    first, second = ids(2)
    mixed = planner.split("US", [(first, BOTH), (second, ("wifi5",))], slow={first})
    assert planner.sweep_ms(mixed[first]) < planner.sweep_ms(mixed[second])
    group = nodes(4)
    slow = ids(4)[0]
    shares = planner.split("US", group, bands={"2.4"}, slow={slow})
    usb = max(planner.sweep_ms(share) for node_id, share in shares.items() if node_id != slow)
    assert planner.sweep_ms(shares[slow]) * planner.AIR_SLOWER <= usb


C3 = ("wifi24", "ble", "espnow")
BLOCK = ("wifi24", "wifi5", "block")


@pytest.mark.parametrize("country", ["US", "DE", "JP", "01"])
def test_2_4_ghz_only_nodes_take_the_2_4_ghz_channels_and_c5s_the_5_ghz_ones(country):
    c5s, c3s = ids(2), ["%012x" % (0xb0b1c2d3e400 + i) for i in range(3)]
    shares = planner.split(country, [(n, BOTH) for n in c5s] + [(n, C3) for n in c3s])
    everything = set(planner.channels(country))
    assert set().union(*map(set, shares.values())) == everything
    for n in c3s:
        assert {band for band, _ in shares[n]} == {"2.4"}
    assert all(band == "5" for n in c5s for band, _ in shares[n])
    for ch in planner.BUSY:
        assert sum(("2.4", ch) in shares[n] for n in c3s) == 2


def test_a_c5_only_takes_2_4_ghz_channels_when_that_finishes_the_sweep_sooner():
    shares = planner.split("01", [("a", BOTH), ("b", BOTH), ("c", BOTH), ("d", C3)])
    assert {band for band, _ in shares["d"]} == {"2.4"}
    assert any(band == "2.4" for n in "abc" for band, _ in shares[n])
    worst = max(planner.sweep_ms(share) for share in shares.values())
    assert worst < planner.sweep_ms(planner.channels("01")) / 2


def test_only_2_4_ghz_nodes_leave_5_ghz_unscanned_and_share_2_4_ghz():
    shares = planner.split("US", [("a", C3), ("b", C3)])
    assert sorted(shares["a"] + shares["b"]) == [("2.4", ch) for ch in range(1, 12)]


def test_block_nodes_get_one_run_of_their_list_and_never_104_or_108():
    shares = planner.split("US", [("a", BLOCK), ("b", BLOCK), ("c", C3)])
    order = [item for item in planner.channels("US") if item[1] not in (104, 108)]
    for n in "ab":
        assert shares[n] and not any(ch in (104, 108) for _, ch in shares[n])
        spots = [order.index(item) for item in shares[n]]
        assert spots == list(range(spots[0], spots[0] + len(spots)))
    assert {band for band, _ in shares["c"]} == {"2.4"} and len(shares["c"]) == 11
    shares = planner.split("US", [("a", BLOCK), ("b", BOTH)])
    assert ("5", 104) in shares["b"] and ("5", 108) in shares["b"]


def test_a_node_heard_through_another_is_counted_slower_than_one_heard_directly():
    direct = planner.split("US", nodes(2), slow=set(ids(2)))
    relayed = planner.split("US", nodes(2), slow=set(ids(2)), relayed={ids(2)[1]})
    assert planner.sweep_ms(relayed[ids(2)[1]]) < planner.sweep_ms(direct[ids(2)[1]])
