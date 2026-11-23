import pytest

from quartermaster import modes
from tests.fakes import adsb_frame, cpr_encode, id13, ident_me, position_me, reply_frame, velocity_me

KLM = "8D4840D6202CC371C32CE0576098"
EVEN = "8D40621D58C382D690C8AC2863A7"
ODD = "8D40621D58C386435CC412692AD6"
GROUND_SPEED = "8D485020994409940838175B284F"
AIRSPEED = "8DA05F219B06B6AF189400CBC33F"
GENERATOR = "1111111111111010000001001"


def divided(data):
    """The parity remainder by long division over the bits, the way the standard writes it."""
    bits = [int(b) for b in "".join("{:08b}".format(x) for x in data)]
    work = bits[:-24] + [0] * 24
    for i in range(len(work) - 24):
        if work[i]:
            for k, g in enumerate(GENERATOR):
                work[i + k] ^= int(g)
    return int("".join(map(str, work[-24:])), 2) ^ int("".join(map(str, bits[-24:])), 2)


def line(hexes):
    return ("*%s;\r\n" % hexes.lower()).encode()


def feed(decoder, *frames, start=100.0, step=1.0):
    for index, frame in enumerate(frames):
        data = bytes.fromhex(frame) if isinstance(frame, str) else frame
        decoder.frame(data, start + index * step)


def test_good_frames_have_no_parity_remainder_and_the_table_agrees_with_long_division():
    for frame in (KLM, EVEN, ODD, GROUND_SPEED, AIRSPEED):
        data = bytes.fromhex(frame)
        assert divided(data) == 0 and modes.syndrome(data) == 0
    for seed in range(200):
        data = bytes((seed * 37 + i * 101) % 256 for i in range(14 if seed % 2 else 7))
        assert modes.syndrome(data) == divided(data)


def test_every_frame_with_one_or_two_bits_wrong_is_rejected():
    good = bytes.fromhex(KLM)
    for bit in range(112):
        bad = bytearray(good)
        bad[bit // 8] ^= 0x80 >> (bit % 8)
        decoder = modes.Decoder()
        assert modes.syndrome(bytes(bad)) != 0 and not decoder.frame(bytes(bad), 0.0) and not decoder.planes
    for first in range(0, 112, 7):
        for second in range(first + 1, 112, 11):
            bad = bytearray(good)
            bad[first // 8] ^= 0x80 >> (first % 8)
            bad[second // 8] ^= 0x80 >> (second % 8)
            assert modes.syndrome(bytes(bad)) != 0


def test_the_klm_identification_gives_its_callsign_and_category():
    decoder = modes.Decoder()
    assert decoder.line(line(KLM), 1.0) and decoder.line(line(KLM), 2.0)
    (report,) = decoder.aircraft(2.5)
    assert report == {"hex": "4840d6", "flight": "KLM1023 ", "category": "A0", "messages": 2, "seen": 0.5}
    assert modes.callsign(ident_me("AB 12")) == "AB 12   " and modes.callsign(ident_me("")) is None
    assert modes.callsign(ident_me("KLM1023") | 0x3F) is None


def test_an_even_and_odd_pair_gives_the_known_position_and_altitude():
    even, odd = bytes.fromhex(EVEN), bytes.fromhex(ODD)
    me_even, me_odd = int.from_bytes(even[4:11], "big"), int.from_bytes(odd[4:11], "big")
    assert ((me_even >> 17) & 0x1FFFF, me_even & 0x1FFFF, (me_even >> 34) & 1) == (93000, 51372, 0)
    assert ((me_odd >> 17) & 0x1FFFF, me_odd & 0x1FFFF, (me_odd >> 34) & 1) == (74158, 50194, 1)
    assert modes.ac12_feet((me_even >> 36) & 0xFFF) == 38000
    lat, lon = modes.cpr_global((93000, 51372), (74158, 50194), False)
    assert lat == pytest.approx(52.2572021484375, abs=1e-9) and lon == pytest.approx(3.91937255859375, abs=1e-9)
    lat, lon = modes.cpr_global((93000, 51372), (74158, 50194), True)
    assert lat == pytest.approx(52.26578017412606, abs=1e-9) and lon == pytest.approx(3.9389125279017856, abs=1e-9)
    assert modes.nl(52.2572021484375) == 36 and modes.nl(0) == 59 and modes.nl(87) == 2 and modes.nl(-88) == 1


def test_one_frame_and_a_nearby_reference_give_the_same_position():
    assert modes.cpr_local(93000, 51372, 0, 52.0, 4.2) == pytest.approx((52.2572021484375, 3.91937255859375))
    assert modes.cpr_local(74158, 50194, 1, 52.5, 3.5) == pytest.approx((52.26578017412606, 3.9389125279017856))
    for lat, lon in ((25.07805, -77.34312), (-33.9, 151.2), (64.1, -21.9), (0.01, 179.99), (-0.3, -179.95)):
        for odd in (0, 1):
            got = modes.cpr_local(*cpr_encode(lat, lon, odd), odd, lat + 0.9, lon - 0.8)
            assert modes.distance_km(lat, lon, *got) < 0.02, (lat, lon, odd, got)


def test_the_decoder_places_the_known_pair_only_after_a_second_fix_agrees():
    decoder = modes.Decoder()
    feed(decoder, ODD, EVEN)
    plane = decoder.planes[0x40621D]
    assert not plane.placed and plane.held[:2] == pytest.approx((52.2572021484375, 3.91937255859375))
    assert "lat" not in decoder.aircraft(101.5)[0]
    feed(decoder, EVEN, start=102.0)
    report = decoder.aircraft(103.0)[0]
    assert (report["lat"], report["lon"], report["alt_baro"], report["seen_pos"]) == (52.257202, 3.919373, 38000, 1.0)


def test_a_pair_further_apart_than_ten_seconds_is_not_decoded_together():
    decoder = modes.Decoder()
    feed(decoder, ODD, EVEN, EVEN, step=10.5)
    plane = decoder.planes[0x40621D]
    assert not plane.placed and plane.held is None


def test_with_the_receiver_known_one_frame_is_still_not_enough_and_the_first_position_comes_from_a_pair():
    decoder = modes.Decoder()
    decoder.receiver = (52.1, 4.4)
    feed(decoder, EVEN)
    assert decoder.planes[0x40621D].held is None
    feed(decoder, ODD, start=101.0)
    plane = decoder.planes[0x40621D]
    assert not plane.placed and plane.held[:2] == pytest.approx((52.26578017412606, 3.9389125279017856))
    feed(decoder, EVEN, start=102.0)
    assert plane.placed and (plane.lat, plane.lon) == pytest.approx((52.2572021484375, 3.91937255859375))


def test_an_aircraft_more_than_half_a_zone_from_the_receiver_is_not_put_at_a_wrong_place_from_single_frames():
    decoder = modes.Decoder()
    decoder.receiver = (40.0, -100.0)
    icao = 0xABCDEF
    real = (43.33, -100.0)
    assert modes.LOCAL_KM < modes.distance_km(40.0, -100.0, *real) < modes.RANGE_KM
    for odd in (0, 1):
        for index in range(4):
            feed(decoder, adsb_frame(icao, position_me(*real, 38000, odd)), start=100.0 + 200 * odd + 15.0 * index)
            assert all("lat" not in report for report in decoder.aircraft(100.0 + 200 * odd + 15.0 * index))
            assert decoder.planes[icao].held is None
    feed(decoder, adsb_frame(icao, position_me(*real, 38000, 0)), start=346.0)
    assert decoder.planes[icao].held[:2] == pytest.approx(real, abs=1e-4)
    feed(decoder, adsb_frame(icao, position_me(*real, 38000, 0)), start=347.0)
    report = decoder.aircraft(347.0)[0]
    assert (report["lat"], report["lon"]) == pytest.approx(real, abs=1e-4)


def test_positions_too_far_from_the_receiver_or_that_jump_are_not_taken():
    far = modes.Decoder()
    far.receiver = (47.5, 3.9)
    feed(far, ODD, EVEN, EVEN)
    assert not far.planes[0x40621D].placed and far.planes[0x40621D].held is None
    feed(far, ODD, start=200.0)
    assert far.planes[0x40621D].held is None
    decoder = modes.Decoder()
    icao = 0xABCDEF
    frames = [adsb_frame(icao, position_me(25.2, -77.7, 30000, odd)) for odd in (0, 1)]
    feed(decoder, *frames)
    feed(decoder, frames[0], start=102.0)
    plane = decoder.planes[icao]
    assert plane.placed and (plane.lat, plane.lon) == pytest.approx((25.2, -77.7), abs=1e-4)
    jump = [adsb_frame(icao, position_me(27.0, -77.7, 30000, odd)) for odd in (0, 1)]
    feed(decoder, jump[0], start=120.0)
    assert not plane.placed and plane.held[:2] == pytest.approx((27.0, -77.7), abs=1e-4)
    assert "lat" not in decoder.aircraft(120.5)[0]
    feed(decoder, jump[1], start=121.0)
    assert plane.placed and plane.lat == pytest.approx(27.0, abs=1e-4)


def test_a_position_more_than_180_nm_from_the_receiver_is_not_decoded_from_one_frame():
    decoder = modes.Decoder()
    decoder.receiver = (25.2, -77.7)
    icao = 0x123456
    feed(decoder, adsb_frame(icao, position_me(25.2 + 3.5, -77.7, 30000, 0)))
    assert decoder.planes[icao].held is None


def test_an_even_and_odd_pair_across_a_zone_boundary_is_not_decoded_together():
    edge = 10.470471299966956
    below, above = cpr_encode(edge - 0.001, 20.0, 0), cpr_encode(edge + 0.001, 20.0, 1)
    assert modes.nl(edge - 0.001) == 59 and modes.nl(edge + 0.001) == 58
    assert modes.cpr_global(below, above, True) is None
    assert modes.cpr_global(cpr_encode(edge - 0.01, 20.0, 0), cpr_encode(edge - 0.01, 20.0, 1), True) == \
        pytest.approx((edge - 0.01, 20.0), abs=1e-4)


def test_ground_speed_track_and_vertical_rate():
    decoder = modes.Decoder()
    feed(decoder, GROUND_SPEED, GROUND_SPEED)
    plane = decoder.planes[0x485020]
    me = int.from_bytes(bytes.fromhex(GROUND_SPEED)[4:11], "big")
    vx, vy = -(((me >> 32) & 0x3FF) - 1), -(((me >> 21) & 0x3FF) - 1)
    assert (vx, vy) == (-8, -159) and (me >> 20) & 1 == 0
    assert plane.gs == pytest.approx(159.2, abs=0.05) and plane.track == pytest.approx(182.9, abs=0.05)
    assert plane.geom_rate == -832 and plane.baro_rate is None
    report = decoder.aircraft(102.0)[0]
    assert (report["gs"], report["track"], report["geom_rate"]) == (159.2, 182.9, -832)


def test_airspeed_and_heading():
    decoder = modes.Decoder()
    feed(decoder, AIRSPEED, AIRSPEED)
    plane = decoder.planes[0xA05F21]
    assert (plane.tas, plane.ias, plane.mag_heading, plane.baro_rate) == (375, None, 244.0, -2304)
    assert plane.gs is None and plane.track is None


def test_ground_speed_made_from_the_spec_round_trips():
    decoder = modes.Decoder()
    for gs, track in ((450, 87.5), (12, 270.0), (300, 0.0), (520, 199.0)):
        feed(decoder, adsb_frame(0x111111, velocity_me(gs, track, rate=-1280)))
        plane = decoder.planes[0x111111]
        assert plane.gs == pytest.approx(gs, abs=1) and plane.baro_rate == -1280
        assert (plane.track - track + 180) % 360 - 180 == pytest.approx(0, abs=0.5)


def test_gillham_altitudes_cover_the_whole_range_and_each_step_changes_one_bit():
    codes = {}
    for code in range(1 << 13):
        if code & 0x50:
            continue
        feet = modes.ac13_feet(code)
        if feet is not None:
            assert feet not in codes, feet
            codes[feet] = code
    assert sorted(codes) == list(range(-1200, 126800, 100))
    for feet in range(-1200, 126700, 100):
        assert bin(codes[feet] ^ codes[feet + 100]).count("1") == 1, feet


def test_25_foot_altitudes():
    for feet in (-1000, -25, 0, 1175, 38000, 50175):
        n = (feet + 1000) // 25
        ac13 = ((n & 0x7E0) << 2) | ((n & 0x10) << 1) | 0x10 | (n & 0xF)
        assert modes.ac13_feet(ac13) == feet
        ac12 = ((n & 0x7F0) << 1) | 0x10 | (n & 0xF)
        assert modes.ac12_feet(ac12) == feet
    assert modes.ac13_feet(0) is None and modes.ac12_feet(0) is None and modes.ac13_feet(0x40 | 0x10) is None


def test_squawks():
    for code in ("7500", "1200", "0000", "7777", "1234", "4061"):
        assert modes.squawk(id13(code)) == code


def altitude13(feet):
    n = (feet + 1000) // 25
    return ((n & 0x7E0) << 2) | ((n & 0x10) << 1) | 0x10 | (n & 0xF)


def test_replies_count_only_for_aircraft_already_heard_with_good_parity():
    decoder = modes.Decoder()
    icao = 0x4840D6
    altitude = altitude13(12000)
    replies = [reply_frame(4, icao, altitude), reply_frame(5, icao, id13("7000")),
               reply_frame(20, icao, altitude), reply_frame(21, icao, id13("2345"))]
    assert not any(decoder.frame(reply, 0.0) for reply in replies) and not decoder.planes
    assert decoder.frame(reply_frame(4, icao, altitude)[:7], 0.0) is False
    feed(decoder, KLM)
    assert decoder.frame(replies[0], 1.0) and decoder.planes[icao].alt_baro is None
    assert decoder.frame(replies[2], 1.5) and decoder.planes[icao].alt_baro == 12000
    assert decoder.frame(replies[1], 2.0) and decoder.frame(replies[1], 2.5) and decoder.planes[icao].squawk == "7000"
    assert decoder.frame(replies[3], 3.0) and decoder.planes[icao].squawk == "7000"
    assert decoder.frame(replies[3], 3.5) and decoder.planes[icao].squawk == "2345"
    bad = bytearray(replies[2])
    bad[13] ^= 1
    assert not decoder.frame(bytes(bad), 4.0)
    assert decoder.planes[icao].messages == 7 and decoder.planes[icao].good == 1
    assert decoder.aircraft(5.0) == []


def test_one_noise_reply_that_matches_an_address_does_not_change_its_squawk_or_altitude():
    decoder = modes.Decoder()
    icao = 0x123456
    feed(decoder, adsb_frame(icao, position_me(25.2, -77.7, 30000, 0)), adsb_frame(icao, ident_me("TST")))
    plane = decoder.planes[icao]
    assert plane.alt_baro == 30000 and plane.squawk is None
    assert decoder.frame(reply_frame(5, icao, id13("7700")), 102.0) and plane.squawk is None
    assert decoder.frame(reply_frame(5, icao, id13("1200")), 103.0) and plane.squawk is None
    assert decoder.frame(reply_frame(4, icao, altitude13(10000)), 104.0) and plane.alt_baro == 30000
    assert decoder.frame(reply_frame(20, icao, altitude13(30200)), 105.0) and plane.alt_baro == 30200
    assert decoder.frame(reply_frame(4, icao, altitude13(50000)), 106.0) and plane.alt_baro == 30200
    assert decoder.frame(reply_frame(4, icao, altitude13(50000)), 107.0) and plane.alt_baro == 50000
    assert decoder.frame(reply_frame(4, icao, altitude13(30000)), 400.0) and plane.alt_baro == 30000
    assert decoder.frame(reply_frame(5, icao, id13("1200")), 401.0) and plane.squawk == "1200"
    assert decoder.aircraft(401.0)[0]["squawk"] == "1200"


def test_an_all_call_reply_with_good_parity_confirms_an_address():
    decoder = modes.Decoder()
    body = bytes([11 << 3 | 5, 0xAB, 0xCD, 0xEF])
    good = body + modes.syndrome(body + b"\0\0\0").to_bytes(3, "big")
    assert decoder.frame(good, 0.0) and decoder.frame(good, 1.0)
    assert decoder.aircraft(1.0) == [{"hex": "abcdef", "messages": 2, "seen": 0.0}]
    interrogated = good[:6] + bytes([good[6] ^ 0x05])
    assert not decoder.frame(interrogated, 2.0)
    long = body + bytes(7)
    assert not decoder.frame(long + modes.syndrome(long + b"\0\0\0").to_bytes(3, "big"), 3.0)


def test_df18_without_an_icao_address_and_wrong_lengths_are_not_taken():
    decoder = modes.Decoder()
    anonymous = bytearray(adsb_frame(0x123456, ident_me("TEST"), df=18))
    assert decoder.frame(bytes(anonymous), 0.0)
    anonymous[0] = 18 << 3 | 1
    anonymous[11:] = modes.syndrome(bytes(anonymous[:11]) + b"\0\0\0").to_bytes(3, "big")
    assert not decoder.frame(bytes(anonymous), 0.0)
    assert not decoder.frame(bytes.fromhex(KLM)[:7], 0.0)


def test_lines_that_are_not_frames_are_skipped():
    decoder = modes.Decoder()
    for raw in (b"", b"\r\n", b"8d4840d6202cc371c32ce0576098;\r\n", b"*8d4840d6202cc371c32ce057609;\r\n",
                b"*8d4840d6202cc371c32ce05760zz;\r\n", b"*8d4840d6202cc371c32ce0576098\r\n", b"DF=17 CA=5\n",
                "*8d4840d6202cc371c32ce05760\u00e9;".encode(), b"*5d 4840d6abcdef;\r\n", b"*5d4840d6ab cd ef;\r\n"):
        assert not decoder.line(raw, 0.0), raw
    assert decoder.line(b"*8d4840d6202cc371c32ce0576098;\n", 0.0)
    assert decoder.line(b"*8D4840D6202CC371C32CE0576098;\r\n", 0.0)


def test_aircraft_go_once_unheard_and_only_recent_ones_are_listed():
    decoder = modes.Decoder()
    feed(decoder, KLM, KLM)
    assert len(decoder.aircraft(101.0 + modes.FRESH_SECONDS - 1)) == 1
    assert decoder.aircraft(101.0 + modes.FRESH_SECONDS + 1) == []
    decoder.forget(101.0 + modes.FORGET_SECONDS + 1)
    assert decoder.planes == {}


def test_at_most_so_many_aircraft_are_kept(monkeypatch):
    monkeypatch.setattr(modes, "MAX_AIRCRAFT", 3)
    decoder = modes.Decoder()
    for index in range(5):
        decoder.frame(adsb_frame(0x100000 + index, ident_me("T%d" % index)), float(index))
    assert sorted(decoder.planes) == [0x100000, 0x100001, 0x100002]
    decoder.frame(adsb_frame(0x200000, ident_me("LATE")), 1000.0)
    assert sorted(decoder.planes) == [0x200000]


def test_geometric_height_is_kept_apart_from_the_barometric_one():
    decoder = modes.Decoder()
    feed(decoder, adsb_frame(0x222222, position_me(25.2, -77.7, 31500, 0, tc=20)),
         adsb_frame(0x222222, position_me(25.2, -77.7, 31500, 1, tc=20)))
    plane = decoder.planes[0x222222]
    assert plane.alt_geom == 31500 and plane.alt_baro is None


def test_an_aircraft_that_moves_on_stays_placed():
    decoder = modes.Decoder()
    decoder.receiver = (25.2, -77.7)
    icao = 0x333333
    for index in range(50):
        lat = 25.2 + index * 0.001
        assert decoder.line(b"*%s;\r\n" % adsb_frame(icao, position_me(lat, -77.7, 30000, index % 2)).hex().encode(),
                            float(index))
        if index >= 2:
            plane = decoder.planes[icao]
            assert plane.placed and plane.lat == pytest.approx(lat, abs=1e-4)
