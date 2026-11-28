#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "enow.h"
#include "proto.h"
#include "seen.h"
#include "wire.h"

static int failures, checks;

#define CHECK(c) check((c), #c, __LINE__)
#define SAME(a, b) same((a), (b), __LINE__)

static void check(int ok, const char *what, int line)
{
    checks++;
    if (!ok) {
        failures++;
        printf("line %d: %s\n", line, what);
    }
}

static void same(const char *got, const char *want, int line)
{
    checks++;
    if (strcmp(got, want)) {
        failures++;
        printf("line %d:\n  got  %s  want %s", line, got, want);
    }
}

static proto_parse_t parse(const char *s, proto_cmd_t *c)
{
    return proto_parse(s, strlen(s), c);
}

static void test_checksum(void)
{
    CHECK(proto_xor("", 0) == 0);
    CHECK(proto_xor("A,17,PLAN,ok,3", 14) == 0x63);
    CHECK(proto_xor("PLAN,3,US,0842,1e,30,80,110,0", 29) == 0x77);
}

static void test_lines(void)
{
    char b[PROTO_LINE_MAX + 1];
    proto_wifi_t w = {
        .bssid = {0xa4, 0x2b, 0xb0, 0x11, 0x22, 0x33}, .ssid = "Home", .ssid_len = 4, .rssi = -61,
        .channel = 6, .auth = 3, .pairwise = 4, .group = 4, .flags = WF_11B | WF_11G | WF_11N,
    };
    CHECK(proto_wifi(b, sizeof(b), 19, 240, &w) > 0);
    SAME(b, "@W,19,240,A4:2B:B0:11:22:33,-61,6,2437,3,4,4,1c,486f6d65*7A\n");

    proto_wifi_t comma = {.ssid = "a,b*\xff", .ssid_len = 5, .rssi = -90, .channel = 149, .flags = WF_FTM};
    proto_wifi(b, sizeof(b), 4294967295u, 0, &comma);
    SAME(b, "@W,4294967295,0,00:00:00:00:00:00,-90,149,5745,0,0,0,100,612c622aff*67\n");

    proto_wifi_t hidden = {.rssi = -1, .channel = 14, .flags = WF_HIDDEN};
    proto_wifi(b, sizeof(b), 1, 2, &hidden);
    SAME(b, "@W,1,2,00:00:00:00:00:00,-1,14,2484,0,0,0,1,*50\n");

    proto_wifi_t full = {.ssid_len = 32, .rssi = -100, .channel = 177};
    memset(full.ssid, 0xee, 32);
    CHECK(proto_wifi(b, sizeof(b), 4294967295u, 4294967295u, &full) > 0);
    CHECK(strlen(b) <= PROTO_LINE_MAX);

    proto_ble_t d = {
        .addr = {0x66, 0x55, 0x44, 0x33, 0x22, 0x11}, .addr_type = 1, .rssi = -70, .flags = BF_CONN | BF_SCAN,
        .has_mfr = 1, .mfr = 0x004c, .name_len = 2, .name = "Hi",
    };
    proto_ble(b, sizeof(b), 7, 12, &d);
    SAME(b, "@B,7,12,11:22:33:44:55:66,1,-70,3,004c,4869*30\n");

    proto_ble_t bare = {.rssi = -99, .flags = BF_RSP};
    proto_ble(b, sizeof(b), 8, 0, &bare);
    SAME(b, "@B,8,0,00:00:00:00:00:00,0,-99,4,,*59\n");

    proto_ble_t big = {.has_mfr = 1, .mfr = 0xffff, .name_len = PROTO_NAME_MAX, .rssi = -127, .flags = 7};
    memset(big.name, 0xab, sizeof(big.name));
    CHECK(proto_ble(b, sizeof(b), 4294967295u, 4294967295u, &big) > 0);
    CHECK(strlen(b) <= PROTO_LINE_MAX);

    proto_status_t s = {51234, 4, 1012, 57, 0, 0, 3, 180224, 1};
    proto_status(b, sizeof(b), 20, &s);
    SAME(b, "@S,20,51234,4,1012,57,0,0,3,180224,run*03\n");

    proto_hello_t h = {
        .id = "a0b1c2d3e4f5", .fw = "1.0.0", .rev_major = 1, .rev_minor = 0, .flash_mb = 8, .psram_mb = 8,
        .board = "c5-8mb", .caps = "wifi24+wifi5+ble", .country = "US", .plan = 3, .running = 0,
    };
    proto_hello(b, sizeof(b), &h);
    char want[PROTO_LINE_MAX + 1];
    const char *body = "H,1,a0b1c2d3e4f5,1.0.0,1.0,8,8,c5-8mb,wifi24+wifi5+ble,US,3,idle";
    snprintf(want, sizeof(want), "@%s*%02X\n", body, proto_xor(body, strlen(body)));
    SAME(b, want);

    proto_ack(b, sizeof(b), 17, "PLAN", 1, "3");
    SAME(b, "@A,17,PLAN,ok,3*63\n");
    proto_ack(b, sizeof(b), 18, "START", 1, "");
    SAME(b, "@A,18,START,ok,*0C\n");
    proto_ack(b, sizeof(b), 1, "X,Y*", 0, "unknown");
    CHECK(strncmp(b, "@A,1,X_Y_,err,unknown*", 22) == 0);
    proto_log(b, sizeof(b), 2, "scan failed, 0x3001*\n");
    CHECK(strncmp(b, "@L,2,scan failed_ 0x3001__*", 27) == 0);

    char tiny[20];
    CHECK(proto_wifi(tiny, sizeof(tiny), 1, 1, &w) < 0);

    CHECK(proto_freq(1) == 2412);
    CHECK(proto_freq(13) == 2472);
    CHECK(proto_freq(36) == 5180);
    CHECK(proto_freq(165) == 5825);
    CHECK(proto_freq(0) == 0);
}

static void test_parse(void)
{
    proto_cmd_t c;
    CHECK(parse("#STOP", &c) == PROTO_CMD && strcmp(c.name, "STOP") == 0 && c.argc == 0);
    CHECK(parse("#STOP\r\n", &c) == PROTO_CMD && strcmp(c.name, "STOP") == 0);
    char line[32];
    snprintf(line, sizeof(line), "#STOP*%02X", proto_xor("STOP", 4));
    CHECK(parse(line, &c) == PROTO_CMD && strcmp(c.name, "STOP") == 0);
    snprintf(line, sizeof(line), "#STOP*%02x", proto_xor("STOP", 4));
    CHECK(parse(line, &c) == PROTO_CMD);
    snprintf(line, sizeof(line), "#STOP*%02X", proto_xor("STOP", 4) ^ 1);
    CHECK(parse(line, &c) == PROTO_BADSUM);
    CHECK(parse("#STOP*1", &c) == PROTO_BADSUM && strcmp(c.name, "STOP") == 0);
    CHECK(parse("#PLAN,1*00", &c) == PROTO_BADSUM && strcmp(c.name, "PLAN") == 0);
    CHECK(parse("#STOP*zz", &c) == PROTO_BADSUM);
    CHECK(parse("@A,1,STOP,ok,*00", &c) == PROTO_IGNORE);
    CHECK(parse("ESP-ROM:esp32c5", &c) == PROTO_IGNORE);
    CHECK(parse("#", &c) == PROTO_IGNORE);
    CHECK(parse("", &c) == PROTO_IGNORE);

    CHECK(parse("#PLAN,3,US,0842,1e,30,80,110,0*77", &c) == PROTO_CMD);
    CHECK(strcmp(c.name, "PLAN") == 0 && c.argc == 8 && strcmp(c.argv[1], "US") == 0 && strcmp(c.argv[7], "0") == 0);
    CHECK(parse("#LED,gpio,,", &c) == PROTO_CMD && c.argc == 3 && *c.argv[1] == 0 && *c.argv[2] == 0);
    CHECK(parse("#X,1,2,3,4,5,6,7,8,9,10,11", &c) == PROTO_CMD && c.argc > PROTO_ARGS_MAX);

    char longline[400];
    memset(longline, 'A', sizeof(longline));
    longline[0] = '#';
    CHECK(proto_parse(longline, sizeof(longline), &c) == PROTO_IGNORE);
}

static const char *plan(const char *line, proto_plan_t *p)
{
    proto_cmd_t c;
    CHECK(parse(line, &c) == PROTO_CMD);
    return proto_plan(&c, p);
}

static void test_plan(void)
{
    proto_plan_t p;
    CHECK(plan("#PLAN,3,US,0842,1e,30,80,110,0", &p) == NULL);
    CHECK(p.version == 3 && strcmp(p.country, "US") == 0 && p.ch2 == 0x842 && p.ch5 == 0x1e);
    CHECK(p.active_min == 30 && p.active_max == 80 && p.passive == 110 && p.ble == 0);
    CHECK(plan("#PLAN,4294967295,01,7ffe,1ffffffe,0,1500,1500,1", &p) == NULL && p.ble == 1);
    CHECK(plan("#PLAN,1,JP,1,1,0,1,1,0", &p) == NULL);

    const char *e;
    CHECK((e = plan("#PLAN,3,US,0842,1e,30,80,110", &p)) && strcmp(e, "args") == 0);
    CHECK((e = plan("#PLAN,0,US,0842,1e,30,80,110,0", &p)) && strcmp(e, "version") == 0);
    CHECK((e = plan("#PLAN,4294967296,US,0842,1e,30,80,110,0", &p)) && strcmp(e, "version") == 0);
    CHECK((e = plan("#PLAN,-1,US,0842,1e,30,80,110,0", &p)) && strcmp(e, "version") == 0);
    CHECK((e = plan("#PLAN,3,ZZ,0842,1e,30,80,110,0", &p)) && strcmp(e, "country") == 0);
    CHECK((e = plan("#PLAN,3,us,0842,1e,30,80,110,0", &p)) && strcmp(e, "country") == 0);
    CHECK((e = plan("#PLAN,3,USA,0842,1e,30,80,110,0", &p)) && strcmp(e, "country") == 0);
    CHECK((e = plan("#PLAN,3,US,8000,1e,30,80,110,0", &p)) && strcmp(e, "bitmap") == 0);
    CHECK((e = plan("#PLAN,3,US,0842,20000000,30,80,110,0", &p)) && strcmp(e, "bitmap") == 0);
    CHECK((e = plan("#PLAN,3,US,08g2,1e,30,80,110,0", &p)) && strcmp(e, "bitmap") == 0);
    CHECK((e = plan("#PLAN,3,US,,1e,30,80,110,0", &p)) && strcmp(e, "bitmap") == 0);
    CHECK((e = plan("#PLAN,3,US,0842,1e,90,80,110,0", &p)) && strcmp(e, "time") == 0);
    CHECK((e = plan("#PLAN,3,US,0842,1e,0,0,110,0", &p)) && strcmp(e, "time") == 0);
    CHECK((e = plan("#PLAN,3,US,0842,1e,30,1501,110,0", &p)) && strcmp(e, "time") == 0);
    CHECK((e = plan("#PLAN,3,US,0842,1e,30,80,0,0", &p)) && strcmp(e, "time") == 0);
    CHECK((e = plan("#PLAN,3,US,0842,1e,30,80,110,2", &p)) && strcmp(e, "ble") == 0);
}

static void test_split(void)
{
    proto_plan_t p;
    uint16_t a2, p2;
    uint32_t a5, p5;
    proto_default_plan(&p, "01");
    proto_split(&p, &a2, &a5, &p2, &p5);
    CHECK(a2 == 0x0ffe && p2 == 0x7000);
    CHECK(a5 == 0x03e0001e && p5 == 0x1c1fffe0);
    p.ch2 = 0x7fff;
    p.ch5 = 0x1e | 1;
    proto_split(&p, &a2, &a5, &p2, &p5);
    CHECK(a2 == 0 && p2 == 0 && a5 == 0 && p5 == 0);
    p.ch2 = 0x1042;
    p.ch5 = 0x20;
    proto_split(&p, &a2, &a5, &p2, &p5);
    CHECK(a2 == 0x42 && p2 == 0x1000 && a5 == 0 && p5 == 0x20);
}

static void test_scan_time(void)
{
    CHECK(proto_scan_ms(0, 0, 1500) == 2000);
    CHECK(proto_scan_ms(0x0002, 0, 100) == 2150);
    CHECK(proto_scan_ms(PROTO_2G_PASSIVE, PROTO_5G_PASSIVE, 1500) == 22 * 1550 + 2000);
    CHECK(proto_scan_ms(PROTO_2G_ALL | 1, PROTO_5G_ALL | 1, 1500) == 42 * 1550 + 2000);
    CHECK(proto_scan_ms(PROTO_2G_ALL & ~PROTO_2G_PASSIVE, PROTO_5G_ALL & ~PROTO_5G_PASSIVE, 1500) > 20 * 1500);
}

static const char *led(const char *line, proto_led_t *l)
{
    proto_cmd_t c;
    CHECK(parse(line, &c) == PROTO_CMD);
    return proto_led(&c, l);
}

static void test_led_and_ident(void)
{
    proto_led_t l;
    CHECK(led("#LED,none", &l) == NULL && l.kind == LED_NONE);
    CHECK(led("#LED,gpio,27,1", &l) == NULL && l.kind == LED_GPIO && l.gpio == 27 && l.active_low == 1);
    CHECK(led("#LED,gpio,8", &l) == NULL && l.gpio == 8 && l.active_low == 0);
    CHECK(led("#LED,ws2812,27,1", &l) == NULL && l.kind == LED_WS2812 && l.active_low == 0);
    CHECK(led("#LED,gpio,13,1", &l) != NULL);
    CHECK(led("#LED,gpio,14", &l) != NULL);
    CHECK(led("#LED,gpio,16", &l) != NULL);
    CHECK(led("#LED,gpio,29", &l) != NULL);
    CHECK(led("#LED,gpio", &l) != NULL);
    CHECK(led("#LED,rgb,27", &l) != NULL);
    CHECK(led("#LED", &l) != NULL);
    CHECK(led("#LED,gpio,27,2", &l) != NULL);
    proto_led_pins((1ULL << 21) | (1ULL << 48));
    CHECK(led("#LED,gpio,21,1", &l) == NULL && l.gpio == 21);
    CHECK(led("#LED,ws2812,48", &l) == NULL && l.gpio == 48);
    CHECK(led("#LED,gpio,27", &l) != NULL);
    CHECK(led("#LED,gpio,49", &l) != NULL);
    proto_led_pins(0x1f8007ffULL);

    proto_cmd_t c;
    unsigned s = 0;
    parse("#IDENT,10", &c);
    CHECK(proto_ident(&c, &s) == NULL && s == 10);
    parse("#IDENT", &c);
    CHECK(proto_ident(&c, &s) == NULL && s == 5);
    parse("#IDENT,0", &c);
    CHECK(proto_ident(&c, &s) != NULL);
    parse("#IDENT,61", &c);
    CHECK(proto_ident(&c, &s) != NULL);
    parse("#IDENT,1,2", &c);
    const char *e = proto_ident(&c, &s);
    CHECK(e && strcmp(e, "seconds") == 0);
}

static void test_adv(void)
{
    proto_adv_t a;
    const uint8_t d[] = {2, 0x01, 0x06, 4, 0x08, 'A', 'b', 'c', 5, 0xff, 0x4c, 0x00, 1, 2, 5, 0x09, 'L', 'o', 'n', 'g'};
    proto_adv_parse(d, sizeof(d), &a);
    CHECK(a.name_len == 4 && memcmp(a.name, "Long", 4) == 0);
    CHECK(a.has_mfr && a.mfr == 0x004c);

    const uint8_t s[] = {3, 0x08, 'H', 'i', 0, 0};
    proto_adv_parse(s, sizeof(s), &a);
    CHECK(a.name_len == 2 && !a.has_mfr);

    const uint8_t late[] = {5, 0x09, 'L', 'o', 'n', 'g', 3, 0x08, 'L', 'o'};
    proto_adv_parse(late, sizeof(late), &a);
    CHECK(a.name_len == 4 && memcmp(a.name, "Long", 4) == 0);

    const uint8_t bad[] = {9, 0x09, 'x'};
    proto_adv_parse(bad, sizeof(bad), &a);
    CHECK(a.name == NULL && !a.has_mfr);
}

static void test_seen(void)
{
    seen_t t;
    uint8_t k1[8] = {1, 2, 3, 4, 5, 6, 0, 0}, k2[8] = {9, 2, 3, 4, 5, 6, 0, 0};
    CHECK(seen_init(&t, 3) == 0);
    CHECK(seen_check(&t, k1, -60, 1000, 0) == SEEN_NEW);
    CHECK(seen_check(&t, k1, -60, 1000, 0) == SEEN_NEW);
    CHECK(seen_note(&t, k1, -60, 1000, 0) == SEEN_NEW);
    CHECK(seen_check(&t, k1, -60, 2000, 0) == SEEN_SKIP);
    CHECK(seen_check(&t, k1, -50, 2000, 0) == SEEN_AGAIN);
    CHECK(seen_note(&t, k1, -60, 2000, 0) == SEEN_SKIP);
    CHECK(seen_note(&t, k1, -55, 3000, 0) == SEEN_AGAIN);
    CHECK(seen_note(&t, k1, -58, 4000, 0) == SEEN_SKIP);
    CHECK(seen_note(&t, k1, -70, 5000, 0) == SEEN_AGAIN);
    CHECK(seen_note(&t, k1, -65, 6000, 0) == SEEN_SKIP);
    CHECK(seen_note(&t, k1, -65, 5000 + SEEN_REFRESH_MS - 1, 0) == SEEN_SKIP);
    CHECK(seen_note(&t, k1, -65, 5000 + SEEN_REFRESH_MS, 0) == SEEN_AGAIN);
    CHECK(seen_note(&t, k1, -65, 5001 + SEEN_REFRESH_MS, 1) == SEEN_AGAIN);
    CHECK(seen_note(&t, k1, -65, 5002 + SEEN_REFRESH_MS, 1) == SEEN_SKIP);
    CHECK(seen_note(&t, k2, -65, 5002 + SEEN_REFRESH_MS, 0) == SEEN_NEW);
    CHECK(t.added == 2 && t.count == 2);

    seen_clear(&t);
    CHECK(t.count == 0 && t.added == 0);
    uint8_t a[8] = {0xa}, b[8] = {0xb}, c[8] = {0xc}, d[8] = {0xd};
    CHECK(seen_note(&t, a, -50, 100, 0) == SEEN_NEW);
    CHECK(seen_note(&t, b, -50, 200, 0) == SEEN_NEW);
    CHECK(seen_note(&t, c, -50, 300, 0) == SEEN_NEW);
    CHECK(seen_note(&t, a, -40, 400, 0) == SEEN_AGAIN);
    CHECK(seen_note(&t, d, -50, 500, 0) == SEEN_NEW);
    CHECK(t.count == 3 && t.added == 4);
    CHECK(seen_note(&t, a, -40, 600, 0) == SEEN_SKIP);
    CHECK(seen_note(&t, c, -50, 600, 0) == SEEN_SKIP);
    CHECK(seen_note(&t, d, -50, 600, 0) == SEEN_SKIP);
    CHECK(seen_note(&t, b, -50, 600, 0) == SEEN_NEW);
    CHECK(seen_note(&t, a, -40, 700, 0) == SEEN_SKIP);
    CHECK(seen_note(&t, c, -50, 700, 0) == SEEN_NEW);
    seen_free(&t);

    CHECK(seen_init(&t, 0) != 0);
    CHECK(seen_init(&t, 2048) == 0);
    uint8_t k[8] = {0};
    for (unsigned i = 0; i < 5000; i++) {
        k[0] = i;
        k[1] = i >> 8;
        CHECK(seen_note(&t, k, -50, 0xfffff000u + i, 0) == SEEN_NEW);
    }
    CHECK(t.count == 2048);
    for (unsigned i = 5000 - 2048; i < 5000; i++) {
        k[0] = i;
        k[1] = i >> 8;
        if (seen_note(&t, k, -50, 0xfffff000u + 5000, 0) != SEEN_SKIP) {
            CHECK(!"recent entry kept");
            break;
        }
    }
    k[0] = 0;
    k[1] = 0;
    CHECK(seen_note(&t, k, -50, 0xfffff000u + 5000, 0) == SEEN_NEW);
    seen_free(&t);
}

static const uint8_t node_mac[6] = {0xa0, 0xb1, 0xc2, 0xd3, 0xe4, 0xf5};

static void test_frames(void)
{
    uint8_t f[WIRE_FRAME_MAX];
    wire_join_t j = {.fw = "1.1.0", .board = "stamp-c5"}, j2;
    memset(j.pub, 0x42, sizeof(j.pub));
    int n = wire_join_put(f, sizeof(f), &j);
    CHECK(n == 84 && f[0] == 'Q' && f[1] == 1 && f[2] == WIRE_JOIN);
    CHECK(wire_join_get(f, n, &j2) == 0 && strcmp(j2.fw, "1.1.0") == 0 && strcmp(j2.board, "stamp-c5") == 0);
    CHECK(memcmp(j2.pub, j.pub, WIRE_PUB) == 0);
    CHECK(wire_join_get(f, n - 1, &j2) < 0);
    f[1] = 2;
    CHECK(wire_type(f, n) < 0 && wire_join_get(f, n, &j2) < 0);
    CHECK(wire_join_put(f, 80, &j) < 0);

    wire_wait_t w = {.channel = 6, .seconds = 300}, w2;
    memcpy(w.to, node_mac, 6);
    n = wire_wait_put(f, sizeof(f), &w);
    CHECK(n == 12 && wire_wait_get(f, n, &w2) == 0 && w2.channel == 6 && w2.seconds == 300);
    CHECK(memcmp(w2.to, node_mac, 6) == 0);
    CHECK(wire_join_get(f, n, &j2) < 0);

    wire_admit_t a = {.channel = 11}, a2;
    memcpy(a.to, node_mac, 6);
    memset(a.pub, 4, sizeof(a.pub));
    memset(a.wrapped, 5, sizeof(a.wrapped));
    memset(a.check, 6, sizeof(a.check));
    n = wire_admit_put(f, sizeof(f), &a);
    CHECK(n == 99 && wire_admit_get(f, n, &a2) == 0 && a2.channel == 11);
    CHECK(memcmp(&a, &a2, sizeof(a)) == 0);

    CHECK(wire_bye_put(f, sizeof(f)) == 3 && wire_type(f, 3) == WIRE_BYE);
    CHECK(wire_type(f, 2) < 0);
}

static void add(wire_out_t *o, uint32_t stamp, const char *line)
{
    CHECK(wire_out_add(o, stamp, line, strlen(line)) == 0);
}

static void test_outbox(void)
{
    uint8_t buf[600], f[WIRE_FRAME_MAX];
    wire_out_t o;
    wire_out_init(&o, buf, sizeof(buf), 7);
    wire_data_t h = {.session = 0x11223344, .now = 5000, .flags = WD_RUN}, d;

    int n = wire_out_frame(&o, f, sizeof(f), &h);
    CHECK(n == WIRE_DATA_HEAD && wire_data_get(f, n, &d) == 0);
    CHECK(d.flags == (WD_RUN | WD_POLL) && d.count == 0 && d.seq == 7 && d.session == 0x11223344 && d.now == 5000);
    CHECK(o.tries == 0);

    add(&o, 4000, "@S,1,2,3,4,5,6,7,8,9,run");
    add(&o, 4500, "@W,2,10,A4:2B:B0:11:22:33,-61,6,2437,3,4,4,1c,486f6d65");
    n = wire_out_frame(&o, f, sizeof(f), &h);
    CHECK(wire_data_get(f, n, &d) == 0 && d.count == 2 && d.seq == 7 && !(d.flags & WD_POLL));
    size_t pos = 0, len;
    uint32_t stamp;
    const char *line;
    CHECK(wire_rec_next(&d, &pos, &stamp, &line, &len) && stamp == 4000 && len == 24 && strncmp(line, "@S,1", 4) == 0);
    CHECK(wire_rec_next(&d, &pos, &stamp, &line, &len) && stamp == 4500 && strncmp(line, "@W,2", 4) == 0);
    CHECK(!wire_rec_next(&d, &pos, &stamp, &line, &len));

    add(&o, 4600, "@L,3,later");
    add(&o, 4650, "@L,4,also");
    CHECK(wire_out_ack(&o, 6, 0) == 0);
    CHECK(wire_out_ack(&o, 3, 0) == 0 && o.sent > 0);
    CHECK(wire_out_ack(&o, 8, 0) == 0 && o.sent > 0);
    CHECK(wire_out_ack(&o, 7, WA_POLL) == 0);
    h.now = 5100;
    n = wire_out_frame(&o, f, sizeof(f), &h);
    CHECK(wire_data_get(f, n, &d) == 0 && d.count == 2 && d.seq == 7 && d.now == 5100 && o.tries == 0);
    wire_out_missed(&o);
    CHECK(o.tries == 1);
    CHECK(wire_out_ack(&o, 7, 0) == 1 && o.seq == 8 && o.sent == 0 && o.tries == 0);
    wire_out_missed(&o);
    CHECK(o.tries == 0);
    n = wire_out_frame(&o, f, sizeof(f), &h);
    CHECK(wire_data_get(f, n, &d) == 0 && d.count == 2 && d.seq == 8);
    for (int i = 0; i < 50; i++) {
        wire_out_frame(&o, f, sizeof(f), &h);
    }
    CHECK(o.tries == 0 && o.lost == 0 && o.seq == 8 && o.len > 0);
    for (int i = 0; i < WIRE_TRIES + 2; i++) {
        wire_out_missed(&o);
        CHECK(o.lost == 0);
    }
    CHECK(o.tries == WIRE_TRIES);
    n = wire_out_frame(&o, f, sizeof(f), &h);
    CHECK(o.lost == 2 && o.seq == 9 && wire_data_get(f, n, &d) == 0 && (d.flags & WD_POLL));

    add(&o, 4700, "@L,5,kept");
    n = wire_out_poll(&o, f, sizeof(f), &h);
    CHECK(n == WIRE_DATA_HEAD && wire_data_get(f, n, &d) == 0 && (d.flags & WD_POLL) && d.seq == 9 && d.count == 0);
    CHECK(o.tries == 0 && wire_out_poll(&o, f, 10, &h) < 0);
    CHECK(wire_out_ack(&o, 9, WA_POLL) == 0 && o.len > 0);
    n = wire_out_frame(&o, f, sizeof(f), &h);
    CHECK(wire_data_get(f, n, &d) == 0 && d.count == 1 && wire_out_ack(&o, 9, 0) == 1 && o.len == 0 && o.seq == 10);

    char big[WIRE_LINE_MAX];
    memset(big, 'x', sizeof(big));
    big[0] = '@';
    CHECK(wire_out_add(&o, 1, big, WIRE_LINE_MAX - 4) == 0);
    CHECK(wire_out_add(&o, 1, big, WIRE_LINE_MAX - 3) < 0);
    CHECK(wire_out_add(&o, 1, big, WIRE_LINE_MAX - 4) == 0);
    CHECK(wire_out_add(&o, 1, big, WIRE_LINE_MAX - 4) < 0);
    CHECK(wire_out_add(&o, 1, big, 0) < 0);
    n = wire_out_frame(&o, f, 250, &h);
    CHECK(n <= 250 && wire_data_get(f, n, &d) == 0 && d.count == 1);
    CHECK(wire_out_ack(&o, 10, 0) == 1);
    n = wire_out_frame(&o, f, sizeof(f), &h);
    CHECK(wire_data_get(f, n, &d) == 0 && d.count == 1 && d.seq == 11);

    f[WIRE_DATA_HEAD] = 200;
    CHECK(wire_data_get(f, n, &d) < 0);
    f[WIRE_DATA_HEAD] = WIRE_LINE_MAX - 4;
    f[17] = 2;
    CHECK(wire_data_get(f, n, &d) < 0);

    n = wire_out_poll(&o, f, sizeof(f), &h);
    memset(f + n, 0, WIRE_REC_HEAD);
    f[17] = 1;
    CHECK(wire_data_get(f, n + WIRE_REC_HEAD, &d) < 0);
    f[n] = 1;
    CHECK(wire_data_get(f, n + WIRE_REC_HEAD, &d) < 0);
    CHECK(wire_data_get(f, n + WIRE_REC_HEAD + 1, &d) == 0 && d.count == 1);
}

static void test_outbox_waits_while_the_hub_is_out_of_reach(void)
{
    uint8_t buf[200], f[WIRE_FRAME_MAX];
    wire_out_t o;
    wire_out_init(&o, buf, sizeof(buf), 1);
    wire_data_t h = {.session = 1};
    unsigned kept = 0;
    char line[40];
    for (int i = 0; i < 100; i++) {
        snprintf(line, sizeof(line), "@L,%d,out of reach", i);
        kept += wire_out_add(&o, i, line, strlen(line)) == 0;
        wire_out_frame(&o, f, sizeof(f), &h);
    }
    CHECK(o.lost == 0 && kept > 0 && kept < 100 && o.len + WIRE_REC_HEAD + 20 > o.cap);
    wire_data_t d;
    unsigned got = 0;
    for (int i = 0; i < 100 && o.len; i++) {
        int n = wire_out_frame(&o, f, sizeof(f), &h);
        CHECK(wire_data_get(f, n, &d) == 0 && wire_out_ack(&o, d.seq, 0) == 1);
        got += d.count;
    }
    CHECK(got == kept && o.len == 0 && o.lost == 0);
}

static void test_peer_and_commands(void)
{
    wire_peer_t p;
    wire_peer_init(&p, node_mac, PEER_NEW, 100);
    wire_data_t d = {.session = 1, .seq = 5};
    CHECK(wire_peer_data(&p, &d, -50, 200) == 1 && p.state == PEER_UP && p.frames == 1 && p.rssi == -50);
    CHECK(wire_peer_data(&p, &d, -51, 300) == 0 && p.resent == 1 && p.heard == 300);
    d.seq = 4;
    CHECK(wire_peer_data(&p, &d, -51, 300) == 0 && p.resent == 2);
    d.seq = 6;
    CHECK(wire_peer_data(&p, &d, -51, 300) == 1);
    d.session = 2;
    d.seq = 0;
    CHECK(wire_peer_data(&p, &d, -51, 300) == 1 && p.frames == 3);
    d.flags = WD_POLL;
    d.seq = 1;
    CHECK(wire_peer_data(&p, &d, -51, 300) == 0 && p.resent == 2);
    d.flags = 0;
    CHECK(wire_peer_data(&p, &d, -51, 300) == 1);

    CHECK(wire_peer_cmd(&p, "#IDENT,5", 8) == 0);
    CHECK(wire_peer_cmd(&p, "#PLAN,3,US,0842,1e,30,80,110,0", 30) == 0);
    CHECK(wire_peer_cmd(&p, "#HELLO", 6) == 0);
    CHECK(wire_peer_cmd(&p, "#HELLO", 6) == 0);
    CHECK(wire_peer_cmd(&p, "#HELLO", 6) < 0);
    CHECK(p.cmd[0].id == 1 && p.cmd[3].id == 4);

    uint8_t f[WIRE_FRAME_MAX];
    wire_ack_t a;
    int n = wire_ack_put(f, sizeof(f), &p, 2, 1, WA_RUN);
    CHECK(wire_ack_get(f, n, &a) == 0 && a.session == 2 && a.seq == 1 && a.flags == WA_RUN && a.ncmd == 4);
    CHECK(a.cmd[1].id == 2 && a.cmd[1].len == 30 && strncmp(a.cmd[1].line, "#PLAN,3,US", 10) == 0);
    CHECK(wire_ack_get(f, n - 1, &a) < 0);
    n = wire_ack_put(f, 30, &p, 2, 1, 0);
    CHECK(wire_ack_get(f, n, &a) == 0 && a.ncmd == 1);

    d.flags = WD_CMD_ACK | WD_POLL;
    d.cmd_ack = 2;
    wire_peer_data(&p, &d, -51, 400);
    CHECK(p.ncmd == 2 && p.cmd[0].id == 3);
    CHECK(wire_peer_cmd(&p, "#LED,none", 9) == 0 && p.cmd[2].id == 5);

    wire_seen_t s = {0};
    CHECK(wire_seen_fresh(&s, 200) == 1);
    CHECK(wire_seen_fresh(&s, 200) == 0);
    CHECK(wire_seen_fresh(&s, 199) == 0);
    CHECK(wire_seen_fresh(&s, 201) == 1);
    CHECK(wire_seen_fresh(&s, 255) == 1);
    CHECK(wire_seen_fresh(&s, 3) == 1);
    CHECK(wire_seen_fresh(&s, 250) == 0);

    wire_node_t nd;
    wire_peer_node(&p, 1400, &nd);
    CHECK(nd.ago == 1000 && nd.frames == 4 && nd.state == PEER_UP && nd.heard_ok);
}

static void test_commands_after_the_hub_starts_again_reach_the_node(void)
{
    wire_seen_t node_cmds = {0};
    CHECK(wire_seen_fresh(&node_cmds, 5));
    wire_peer_t p;
    wire_peer_init(&p, node_mac, PEER_UP, 100);
    CHECK(wire_peer_cmd(&p, "#PLAN,3,US,0842,1e,30,80,110,0", 30) == 0 && p.cmd[0].id == 1);
    wire_data_t d = {.session = 9, .seq = 40, .cmd_ack = 5, .flags = WD_CMD_ACK | WD_RUN};
    CHECK(wire_peer_data(&p, &d, -50, 200) == 1);
    CHECK(p.ncmd == 1 && p.cmd[0].id == 6 && p.next_id == 7);
    CHECK(wire_peer_cmd(&p, "#HELLO", 6) == 0 && p.cmd[1].id == 7);

    uint8_t f[WIRE_FRAME_MAX];
    wire_ack_t a;
    int n = wire_ack_put(f, sizeof(f), &p, 9, 40, 0);
    CHECK(wire_ack_get(f, n, &a) == 0 && a.ncmd == 2);
    CHECK(wire_seen_fresh(&node_cmds, a.cmd[0].id) && wire_seen_fresh(&node_cmds, a.cmd[1].id));

    d.seq = 41;
    d.cmd_ack = 7;
    wire_peer_data(&p, &d, -50, 300);
    CHECK(p.ncmd == 0);
    d.cmd_ack = 5;
    CHECK(wire_peer_cmd(&p, "#HELLO", 6) == 0 && p.cmd[0].id == 8);
    d.seq = 42;
    wire_peer_data(&p, &d, -50, 400);
    CHECK(p.ncmd == 1 && p.cmd[0].id == 8);

    wire_peer_init(&p, node_mac, PEER_NEW, 500);
    CHECK(wire_peer_cmd(&p, "#HELLO", 6) == 0);
    d.flags = WD_POLL;
    wire_peer_data(&p, &d, -50, 600);
    CHECK(p.ncmd == 1 && p.cmd[0].id == 1 && p.synced);
}

static void test_a_late_send_report_is_not_taken_for_the_next_send(void)
{
    wire_tx_t t;
    memset(&t, 0, sizeof(t));
    CHECK(wire_tx_ready(&t, 0));
    wire_tx_issued(&t, 100);
    CHECK(wire_tx_result(&t) < 0 && !wire_tx_ready(&t, 160));
    wire_tx_done(&t, 1);
    CHECK(wire_tx_result(&t) == 1 && wire_tx_ready(&t, 170));
    wire_tx_issued(&t, 170);
    CHECK(wire_tx_result(&t) < 0);
    wire_tx_done(&t, 0);
    CHECK(wire_tx_result(&t) == 0);

    wire_tx_issued(&t, 200);
    CHECK(!wire_tx_ready(&t, 200 + WIRE_TX_STALE_MS - 1));
    CHECK(wire_tx_ready(&t, 200 + WIRE_TX_STALE_MS));
    wire_tx_done(&t, 1);
    CHECK(t.done == t.issued);
    wire_tx_issued(&t, 1300);
    CHECK(wire_tx_result(&t) < 0);
}

static void test_the_button_counts_only_after_it_was_seen_up(void)
{
    wire_button_t b;
    memset(&b, 0, sizeof(b));
    CHECK(!wire_button(&b, 0, 0) && !wire_button(&b, 0, 5000) && !wire_button(&b, 0, 60000));
    CHECK(!wire_button(&b, 1, 60100));
    CHECK(!wire_button(&b, 0, 61000) && !wire_button(&b, 0, 61000 + WIRE_HOLD_MS - 1));
    CHECK(wire_button(&b, 0, 61000 + WIRE_HOLD_MS));
    CHECK(!wire_button(&b, 0, 70000));
    CHECK(!wire_button(&b, 1, 70100) && !wire_button(&b, 0, 70200) && !wire_button(&b, 0, 71000));
    CHECK(wire_button(&b, 0, 70200 + WIRE_HOLD_MS));
}

static void test_pairing(void)
{
    wire_pair_t p;
    memset(&p, 0, sizeof(p));
    wire_join_t j = {.fw = "1.1.0", .board = "c5"};
    int tell;
    CHECK(wire_pair_heard(&p, node_mac, -40, &j, 1000, &tell) == NULL && !tell);
    wire_pair_open(&p, 0xfffff000u, 60);
    CHECK(wire_pair_left(&p, 0xfffff000u) == 60);
    CHECK(wire_pair_left(&p, 0xfffff000u + 59001) == 1);
    wire_cand_t *c = wire_pair_heard(&p, node_mac, -40, &j, 0xfffff100u, &tell);
    CHECK(c && tell && c->rssi == -40);
    CHECK(wire_pair_heard(&p, node_mac, -41, &j, 0xfffff100u + 4999, &tell) == c && !tell && c->rssi == -41);
    CHECK(wire_pair_heard(&p, node_mac, -41, &j, 0xfffff100u + 5000, &tell) == c && tell);
    CHECK(wire_pair_find(&p, node_mac, 0xfffff100u + 5000 + WIRE_JOIN_KEEP_MS - 1) == c);
    CHECK(wire_pair_find(&p, node_mac, 0xfffff100u + 5000 + WIRE_JOIN_KEEP_MS) == NULL);
    uint8_t other[6] = {1, 2, 3, 4, 5, 6};
    CHECK(wire_pair_find(&p, other, 0xfffff200u) == NULL);
    wire_pair_open(&p, 10000, 60);
    CHECK(wire_pair_heard(&p, node_mac, -40, &j, 10000, &tell) && tell);
    for (int i = 0; i < WIRE_NODES - 1; i++) {
        other[5] = i;
        CHECK(wire_pair_heard(&p, other, -60, &j, 11000 + i, &tell) && tell);
    }
    CHECK(wire_pair_find(&p, node_mac, 12000) != NULL);
    other[5] = WIRE_NODES - 1;
    CHECK(wire_pair_heard(&p, other, -60, &j, 12000, &tell) && tell);
    CHECK(wire_pair_find(&p, node_mac, 12000) == NULL);
    other[5] = 0;
    CHECK(wire_pair_find(&p, other, 12000) != NULL);
    CHECK(wire_pair_left(&p, 70000) == 0 && !p.open);
    CHECK(wire_pair_find(&p, other, 12000) == NULL);
    wire_pair_open(&p, 5000, 10);
    CHECK(wire_pair_heard(&p, node_mac, -40, &j, 5001, &tell) && tell);
    wire_pair_open(&p, 6000, 0);
    CHECK(wire_pair_left(&p, 6000) == 0 && wire_pair_find(&p, node_mac, 6000) == NULL);
}

static void test_air_states(void)
{
    wire_air_t a;
    wire_air_init(&a, 0, 6);
    CHECK(a.state == AIR_HOP);
    CHECK(wire_air_next(&a, 0) == 7);
    for (int i = 0; i < 4; i++) {
        wire_air_next(&a, 0);
    }
    CHECK(wire_air_next(&a, 0) == 1);
    wire_air_wait(&a, 3, 1000);
    CHECK(a.state == AIR_WAIT && wire_air_next(&a, 1000 + WIRE_WAIT_MS - 1) == 3);
    CHECK(wire_air_next(&a, 1000 + WIRE_WAIT_MS) == 4 && a.state == AIR_HOP);
    wire_air_linked(&a, 9, 2000);
    wire_air_wait(&a, 3, 2000);
    CHECK(a.state == AIR_LINKED && wire_air_next(&a, 2000) == 9);

    wire_air_init(&a, 1, 0);
    CHECK(a.state == AIR_SEARCH && a.home == WIRE_HOME_DEFAULT);
    wire_air_init(&a, 1, 11);
    int home = 0, others = 0;
    for (int i = 0; i < WIRE_SEARCH_HOME + WIRE_HOME_MAX; i++) {
        uint8_t ch = wire_air_next(&a, 0);
        CHECK(ch >= 1 && ch <= WIRE_HOME_MAX);
        if (i < WIRE_SEARCH_HOME) {
            CHECK(ch == 11);
        }
        home += ch == 11;
        others += ch != 11;
    }
    CHECK(home == WIRE_SEARCH_HOME + 1 && others == WIRE_HOME_MAX - 1);
    CHECK(wire_air_next(&a, 0) == 11);
    CHECK(wire_air_lost(&a, 0) == 0);
    wire_air_linked(&a, 1, 0xffffff00u);
    CHECK(a.home == 1 && wire_air_lost(&a, 0xffffff00u + WIRE_SILENCE_MS - 1) == 0);
    CHECK(wire_air_lost(&a, 0xffffff00u + WIRE_SILENCE_MS) == 1 && a.state == AIR_SEARCH);
    CHECK(wire_air_next(&a, 0) == 1);

    wire_air_init(&a, 1, 6);
    CHECK(wire_air_run(&a) == -1);
    wire_air_answer(&a, WA_POLL);
    wire_air_answer(&a, WA_RUN | WA_POLL);
    CHECK(wire_air_run(&a) == 1 && wire_air_run(&a) == -1);
    wire_air_answer(&a, WA_POLL);
    CHECK(wire_air_run(&a) == 0);
    wire_air_answer(&a, WA_RUN);
    wire_air_init(&a, 0, 6);
    CHECK(wire_air_run(&a) == -1);
}

static void test_chunks(void)
{
    uint16_t c2 = 0x0ffe, o2;
    uint32_t c5 = 0x0000001e, o5;
    unsigned n = wire_chunk_size(80);
    CHECK(n == 6 && wire_chunk_size(110) == 4 && wire_chunk_size(1500) == 1 && wire_chunk_size(0) == 30);
    wire_chunk(&c2, &c5, n, &o2, &o5);
    CHECK(o2 == 0x007e && o5 == 0 && c2 == 0x0f80);
    wire_chunk(&c2, &c5, n, &o2, &o5);
    CHECK(o2 == 0x0f80 && o5 == 0x02 && c2 == 0 && c5 == 0x1c);
    wire_chunk(&c2, &c5, n, &o2, &o5);
    CHECK(o2 == 0 && o5 == 0x1c && c5 == 0);
    wire_chunk(&c2, &c5, n, &o2, &o5);
    CHECK(o2 == 0 && o5 == 0);
}

static const char *hub_args(const char *line, uint8_t *home, uint8_t *key)
{
    proto_cmd_t c;
    CHECK(parse(line, &c) == PROTO_CMD);
    return wire_hub_args(&c, home, key);
}

static void test_hub_commands(void)
{
    uint8_t home = 0, key[WIRE_KEY], mac[6];
    const char *e;
    CHECK(hub_args("#HUB,6,000102030405060708090a0b0c0d0e0F", &home, key) == NULL && home == 6);
    CHECK(key[0] == 0 && key[15] == 0x0f);
    CHECK(hub_args("#HUB,,ffffffffffffffffffffffffffffffff", &home, key) == NULL && home == WIRE_HOME_DEFAULT);
    CHECK(hub_args("#HUB,11,ffffffffffffffffffffffffffffffff", &home, key) == NULL && home == 11);
    CHECK((e = hub_args("#HUB,12,ffffffffffffffffffffffffffffffff", &home, key)) && strcmp(e, "channel") == 0);
    CHECK((e = hub_args("#HUB,0,ffffffffffffffffffffffffffffffff", &home, key)) && strcmp(e, "channel") == 0);
    CHECK((e = hub_args("#HUB,6x,ffffffffffffffffffffffffffffffff", &home, key)) && strcmp(e, "channel") == 0);
    CHECK((e = hub_args("#HUB,6,00000000000000000000000000000000", &home, key)) && strcmp(e, "key") == 0);
    CHECK((e = hub_args("#HUB,6,fffffffffffffffffffffffffffffff", &home, key)) && strcmp(e, "key") == 0);
    CHECK((e = hub_args("#HUB,6,fffffffffffffffffffffffffffffffg", &home, key)) && strcmp(e, "key") == 0);
    CHECK((e = hub_args("#HUB,6", &home, key)) && strcmp(e, "args") == 0);

    proto_cmd_t c;
    unsigned s = 1;
    parse("#PAIR", &c);
    CHECK(wire_pair_args(&c, &s) == NULL && s == WIRE_PAIR_DEFAULT);
    parse("#PAIR,0", &c);
    CHECK(wire_pair_args(&c, &s) == NULL && s == 0);
    parse("#PAIR,600", &c);
    CHECK(wire_pair_args(&c, &s) == NULL && s == 600);
    parse("#PAIR,601", &c);
    CHECK(wire_pair_args(&c, &s) != NULL);
    parse("#PAIR,-1", &c);
    CHECK(wire_pair_args(&c, &s) != NULL);
    parse("#PAIR,1,2", &c);
    CHECK((e = wire_pair_args(&c, &s)) && strcmp(e, "seconds") == 0);

    char line[WIRE_CMD_MAX];
    parse("#TO,A0b1c2d3e4f5,PLAN,3,US,0842,1e,30,80,110,1", &c);
    CHECK(wire_to_args(&c, mac, line, sizeof(line)) == NULL && memcmp(mac, node_mac, 6) == 0);
    SAME(line, "#PLAN,3,US,0842,1e,30,80,110,1");
    parse("#TO,a0b1c2d3e4f5,HELLO", &c);
    CHECK(wire_to_args(&c, mac, line, sizeof(line)) == NULL);
    SAME(line, "#HELLO");
    parse("#TO,a0b1c2d3e4f5,IDENT,", &c);
    CHECK(wire_to_args(&c, mac, line, sizeof(line)) == NULL);
    SAME(line, "#IDENT,");
    parse("#TO,a0b1c2d3e4f5,BOOTLOADER", &c);
    CHECK((e = wire_to_args(&c, mac, line, sizeof(line))) && strcmp(e, "command") == 0);
    parse("#TO,a0b1c2d3e4f5,START", &c);
    CHECK((e = wire_to_args(&c, mac, line, sizeof(line))) && strcmp(e, "command") == 0);
    parse("#TO,a0b1c2d3e4f,HELLO", &c);
    CHECK((e = wire_to_args(&c, mac, line, sizeof(line))) && strcmp(e, "node") == 0);
    parse("#TO,a0b1c2d3e4f5", &c);
    CHECK((e = wire_to_args(&c, mac, line, sizeof(line))) && strcmp(e, "args") == 0);
    CHECK(wire_remote_ok("REBOOT") && !wire_remote_ok("HUB") && !wire_remote_ok("TO"));

    CHECK(wire_id("A0B1C2D3E4F5", mac) == 0 && memcmp(mac, node_mac, 6) == 0);
    CHECK(wire_id("a0b1c2d3e4f", mac) < 0 && wire_id("a0b1c2d3e4fz", mac) < 0 && wire_id(NULL, mac) < 0);
    char id[13];
    wire_hexid(node_mac, id);
    SAME(id, "a0b1c2d3e4f5");
}

static void test_relay(void)
{
    char b[PROTO_LINE_MAX + 1], inner[PROTO_LINE_MAX + 1], id[13], want[PROTO_LINE_MAX + 1];
    const char *w = "@W,19,240,A4:2B:B0:11:22:33,-61,6,2437,3,4,4,1c,486f6d65";
    int n = wire_relay(b, sizeof(b), node_mac, w, strlen(w), 1500);
    const char *body = "R,a0b1c2d3e4f5,@W,19,1740,A4:2B:B0:11:22:33,-61,6,2437,3,4,4,1c,486f6d65";
    snprintf(want, sizeof(want), "@%s*%02X\n", body, proto_xor(body, strlen(body)));
    CHECK(n == (int)strlen(want));
    SAME(b, want);
    CHECK(wire_unrelay(b, n, id, inner, sizeof(inner)) > 0);
    SAME(id, "a0b1c2d3e4f5");
    const char *fixed = "W,19,1740,A4:2B:B0:11:22:33,-61,6,2437,3,4,4,1c,486f6d65";
    snprintf(want, sizeof(want), "@%s*%02X\n", fixed, proto_xor(fixed, strlen(fixed)));
    SAME(inner, want);

    const char *s = "@S,20,51234,4,1012,57,0,0,3,180224,run";
    wire_relay(b, sizeof(b), node_mac, s, strlen(s), 999);
    CHECK(wire_unrelay(b, strlen(b), id, inner, sizeof(inner)) > 0);
    SAME(inner, "@S,20,51234,4,1012,57,0,0,3,180224,run*03\n");

    const char *bl = "@B,8,4294967000,00:00:00:00:00:00,0,-99,4,,";
    wire_relay(b, sizeof(b), node_mac, bl, strlen(bl), 1000);
    CHECK(wire_unrelay(b, strlen(b), id, inner, sizeof(inner)) > 0);
    CHECK(strncmp(inner, "@B,8,4294967295,00:00", 21) == 0);

    const char *h = "@H,1,a0b1c2d3e4f5,1.1.0,1.0,8,8,c5-8mb,wifi24+wifi5+ble+espnow,US,3,idle";
    wire_relay(b, sizeof(b), node_mac, h, strlen(h), 5);
    CHECK(wire_unrelay(b, strlen(b), id, inner, sizeof(inner)) > 0 && strncmp(inner, h, strlen(h)) == 0);

    CHECK(wire_relay(b, sizeof(b), node_mac, "@W,1*", 5, 0) < 0);
    CHECK(wire_relay(b, sizeof(b), node_mac, "W,1,2", 5, 0) < 0);
    CHECK(wire_relay(b, 20, node_mac, w, strlen(w), 0) < 0);

    char longest[PROTO_LINE_MAX + 1];
    proto_ble_t big = {.has_mfr = 1, .mfr = 0xffff, .name_len = PROTO_NAME_MAX, .rssi = -127, .flags = 7};
    memset(big.name, 0xab, sizeof(big.name));
    n = proto_ble(longest, sizeof(longest), 4294967295u, 0, &big);
    CHECK(n > 0 && n <= WIRE_LINE_MAX);
    CHECK(wire_relay(b, sizeof(b), node_mac, longest, n - 4, 4294967295u) > 0 && strlen(b) <= PROTO_LINE_MAX);

    b[5] ^= 1;
    CHECK(wire_unrelay(b, strlen(b), id, inner, sizeof(inner)) < 0);
    CHECK(wire_unrelay("@R,a0b1c2d3e4f5,W,1*00\n", 23, id, inner, sizeof(inner)) < 0);
    CHECK(wire_unrelay("@A,1,X,ok,*00\n", 14, id, inner, sizeof(inner)) < 0);
}

static void test_hub_lines(void)
{
    char b[PROTO_LINE_MAX + 1], want[PROTO_LINE_MAX + 1];
    wire_cluster_t c = {.home = 6, .left = 45, .nodes = 2, .running = 1, .relayed = 1234, .dropped = 3};
    CHECK(wire_cluster_line(b, sizeof(b), 40, &c) > 0);
    const char *body = "C,40,6,45,2,run,1234,3";
    snprintf(want, sizeof(want), "@%s*%02X\n", body, proto_xor(body, strlen(body)));
    SAME(b, want);

    wire_node_t n = {.state = PEER_UP, .heard_ok = 1, .rssi = -67, .ago = 850, .frames = 120, .resent = 4,
                     .hops = 1};
    memcpy(n.mac, node_mac, 6);
    wire_node_line(b, sizeof(b), 41, &n);
    CHECK(strncmp(b, "@N,41,a0b1c2d3e4f5,up,850,-67,120,4,,1*", 39) == 0);
    n.ago = WIRE_SILENCE_MS;
    wire_node_line(b, sizeof(b), 42, &n);
    CHECK(strncmp(b, "@N,42,a0b1c2d3e4f5,lost,60000,-67,120,4,,1*", 43) == 0);
    n.state = PEER_NEW;
    n.heard_ok = 0;
    n.frames = n.resent = 0;
    wire_node_line(b, sizeof(b), 43, &n);
    CHECK(strncmp(b, "@N,43,a0b1c2d3e4f5,new,,,0,0,,1*", 32) == 0);
    n.state = PEER_UP;
    n.heard_ok = 1;
    n.ago = 20;
    n.has_via = 1;
    n.hops = 3;
    memcpy(n.via, (const uint8_t[]){0x10, 0x20, 0x30, 0x40, 0x50, 0x60}, 6);
    wire_node_line(b, sizeof(b), 44, &n);
    CHECK(strncmp(b, "@N,44,a0b1c2d3e4f5,up,20,-67,0,0,102030405060,3*", 48) == 0);

    wire_joiner_t j = {.rssi = -48, .fw = "1.1.0", .board = "c5,*x"};
    memcpy(j.mac, node_mac, 6);
    wire_join_line(b, sizeof(b), 44, &j);
    CHECK(strncmp(b, "@J,44,a0b1c2d3e4f5,-48,1.1.0,c5__x*", 35) == 0);
}

static void test_seen_floor_keeps_the_far_edge(void)
{
    seen_t t;
    uint8_t k[SEEN_KEY] = {9};
    CHECK(seen_init(&t, 4) == 0);
    CHECK(seen_note(&t, k, -80, 1000, 0) == SEEN_NEW);
    CHECK(seen_note(&t, k, -95, 2000, 0) == SEEN_SKIP);
    CHECK(seen_note(&t, k, -90, 3000, 0) == SEEN_AGAIN);
    CHECK(seen_note(&t, k, -91, 4000, 0) == SEEN_AGAIN);
    CHECK(seen_note(&t, k, -91, 5000, 0) == SEEN_SKIP);
    CHECK(seen_note(&t, k, -92, 6000, 0) == SEEN_AGAIN);
    CHECK(seen_note(&t, k, -99, 7000, 0) == SEEN_SKIP);
    CHECK(seen_note(&t, k, -92, 8000, 0) == SEEN_SKIP);
    CHECK(seen_note(&t, k, -99, 6000 + SEEN_REFRESH_MS, 0) == SEEN_AGAIN);
    CHECK(seen_note(&t, k, -92, 6001 + SEEN_REFRESH_MS, 0) == SEEN_SKIP);

    seen_clear(&t);
    CHECK(seen_note(&t, k, -80, 1000, 0) == SEEN_NEW);
    CHECK(seen_note(&t, k, -90, 2000, 0) == SEEN_AGAIN);
    CHECK(seen_note(&t, k, -95, 3000, 0) == SEEN_SKIP);
    CHECK(seen_check(&t, k, -95, 2000 + SEEN_REFRESH_MS - 1, 0) == SEEN_SKIP);

    seen_clear(&t);
    CHECK(seen_note(&t, k, -96, 1000, 0) == SEEN_NEW);
    CHECK(seen_note(&t, k, -97, 2000, 0) == SEEN_SKIP);
    CHECK(seen_note(&t, k, -93, 3000, 0) == SEEN_AGAIN);
    CHECK(seen_note(&t, k, -94, 4000, 0) == SEEN_SKIP);
    CHECK(seen_note(&t, k, -88, 5000, 0) == SEEN_AGAIN);
    CHECK(seen_note(&t, k, -92, 6000, 0) == SEEN_AGAIN);
    CHECK(seen_note(&t, k, -90, 7000, 0) == SEEN_SKIP);
    seen_free(&t);
}

static void test_seen_reports_the_first_manufacturer(void)
{
    seen_t t;
    uint8_t k[SEEN_KEY] = {7, 1};
    CHECK(seen_init(&t, 4) == 0);
    CHECK(seen_note(&t, k, -60, 1000, 0) == SEEN_NEW);
    CHECK(seen_check(&t, k, -60, 1100, 2) == SEEN_AGAIN);
    CHECK(seen_note(&t, k, -60, 1100, 2) == SEEN_AGAIN);
    CHECK(seen_note(&t, k, -60, 1200, 0) == SEEN_SKIP);
    CHECK(seen_note(&t, k, -60, 1300, 2) == SEEN_SKIP);
    CHECK(seen_note(&t, k, -60, 1400, 1) == SEEN_AGAIN);
    CHECK(seen_note(&t, k, -60, 1500, 3) == SEEN_SKIP);
    seen_free(&t);
}

static void test_a_busy_hub_keeps_the_lines_on_the_node(void)
{
    uint8_t buf[600], f[WIRE_FRAME_MAX];
    wire_out_t o;
    wire_out_init(&o, buf, sizeof(buf), 3);
    wire_data_t h = {.session = 9, .now = 5000}, d;
    const char *l1 = "@W,2,10,A4:2B:B0:11:22:33,-61,6,2437,3,4,4,1c,486f6d65";
    const char *l2 = "@L,3,later";
    add(&o, 4000, l1);
    add(&o, 4500, l2);
    int n = wire_out_frame(&o, f, sizeof(f), &h);
    CHECK(wire_data_get(f, n, &d) == 0 && d.count == 2);

    char r[PROTO_LINE_MAX + 1];
    size_t want = WIRE_RING_ITEM(wire_relay(r, sizeof(r), node_mac, l1, strlen(l1), 1000));
    want += WIRE_RING_ITEM(wire_relay(r, sizeof(r), node_mac, l2, strlen(l2), 500));
    CHECK(wire_relay_need(node_mac, &d) == want && want % 4 == 0 && want > strlen(l1) + strlen(l2));
    CHECK(WIRE_RING_ITEM(1) == 12 && WIRE_RING_ITEM(4) == 12 && WIRE_RING_ITEM(5) == 16);

    wire_peer_t p;
    wire_peer_init(&p, node_mac, PEER_UP, 100);
    CHECK(wire_peer_fresh(&p, &d));
    wire_data_t held = d;
    held.flags |= WD_POLL;
    CHECK(wire_peer_data(&p, &held, -50, 200) == 0 && !p.have_seq && p.frames == 0 && p.resent == 0);
    int k = wire_ack_put(f, sizeof(f), &p, d.session, d.seq, WA_RUN | WA_POLL | WA_BUSY);
    wire_ack_t a;
    CHECK(wire_ack_get(f, k, &a) == 0 && (a.flags & WA_BUSY));
    CHECK(wire_out_ack(&o, a.seq, a.flags) == 0 && o.sent > 0 && o.tries == 0 && o.lost == 0);

    n = wire_out_frame(&o, f, sizeof(f), &h);
    CHECK(wire_data_get(f, n, &d) == 0 && d.count == 2 && d.seq == 3);
    CHECK(wire_peer_fresh(&p, &d) && wire_peer_data(&p, &d, -50, 300) == 1);
    CHECK(!wire_peer_fresh(&p, &d) && wire_peer_data(&p, &d, -50, 300) == 0 && p.resent == 1);
    CHECK(wire_out_ack(&o, d.seq, WA_RUN) == 1 && o.len == 0);

    n = wire_out_poll(&o, f, sizeof(f), &h);
    CHECK(wire_data_get(f, n, &d) == 0 && !wire_peer_fresh(&p, &d) && wire_relay_need(node_mac, &d) == 0);
}

static void test_a_second_key_under_one_node_id_is_refused(void)
{
    wire_pair_t p;
    memset(&p, 0, sizeof(p));
    wire_join_t real = {.fw = "1.1.0", .board = "c5"}, fake = real;
    real.pub[0] = 4;
    real.pub[1] = 1;
    fake.pub[0] = 4;
    fake.pub[1] = 2;
    int tell;
    wire_pair_open(&p, 1000, 60);
    wire_cand_t *c = wire_pair_heard(&p, node_mac, -40, &real, 1000, &tell);
    CHECK(c && tell && c->join.pub[1] == 1);
    CHECK(wire_pair_heard(&p, node_mac, -40, &real, 2000, &tell) == c && !tell);
    CHECK(wire_pair_heard(&p, node_mac, -30, &fake, 3000, &tell) == NULL && !tell);
    CHECK(c->clash && c->join.pub[1] == 1 && c->rssi == -40);
    CHECK(wire_pair_find(&p, node_mac, 3000) == NULL);
    CHECK(wire_pair_heard(&p, node_mac, -40, &real, 9000, &tell) == NULL && !tell);
    CHECK(wire_pair_find(&p, node_mac, 9000) == NULL);

    uint8_t other[6] = {1, 2, 3, 4, 5, 0};
    for (int i = 0; i < WIRE_NODES + 3; i++) {
        other[5] = i;
        CHECK(wire_pair_heard(&p, other, -60, &real, 10000 + i, &tell) != NULL);
    }
    CHECK(wire_pair_heard(&p, node_mac, -40, &fake, 20000, &tell) == NULL && c->clash);

    wire_pair_open(&p, 30000, 0);
    wire_pair_open(&p, 30000, 60);
    c = wire_pair_heard(&p, node_mac, -40, &fake, 30000, &tell);
    CHECK(c && tell && !c->clash && wire_pair_find(&p, node_mac, 30000) == c);
}

static void test_relayed_answers(void)
{
    uint8_t f[WIRE_FRAME_MAX];
    wire_peer_t p;
    wire_peer_init(&p, node_mac, PEER_UP, 0);
    wire_peer_cmd(&p, "#IDENT,5", 8);
    int n = wire_ack_put(f, sizeof(f), &p, 7, 3, WA_RUN);
    wire_ack_t *a = calloc(1, sizeof(*a));
    CHECK(wire_ack_get(f, n, a) == 0 && a->ncmd == 1 && a->next_ms == 0 && a->ncarried == 0);
    n = wire_ack_relay(f, sizeof(f), n, 640);
    CHECK(n > 0 && wire_ack_get(f, n, a) == 0 && a->next_ms == 640 && a->ncarried == 0 && a->ncmd == 1);
    const uint8_t kid[6] = {1, 2, 3, 4, 5, 6}, far[6] = {6, 5, 4, 3, 2, 1};
    wire_queued_t q[2] = {{.id = 9, .len = 6, .line = "#HELLO"}, {.id = 10, .len = 8, .line = "#IDENT,3"}};
    n = wire_ack_carry(f, sizeof(f), n, kid, WC_CMDS, q, 2);
    n = wire_ack_carry(f, sizeof(f), n, far, WC_BYE, NULL, 0);
    CHECK(n > 0 && wire_ack_get(f, n, a) == 0 && a->ncarried == 2);
    CHECK(memcmp(a->carried[0].mac, kid, 6) == 0 && a->carried[0].kind == WC_CMDS && a->carried[0].ncmd == 2);
    CHECK(a->carried[0].cmd[1].id == 10 && a->carried[0].cmd[1].len == 8 &&
          memcmp(a->carried[0].cmd[1].line, "#IDENT,3", 8) == 0);
    CHECK(a->carried[1].kind == WC_BYE && a->carried[1].ncmd == 0);
    CHECK(wire_ack_get(f, n - 1, a) < 0);
    CHECK(wire_ack_carry(f, n + 5, n, kid, WC_CMDS, q, 2) < 0);
    f[n - 2] = 7;
    CHECK(wire_ack_get(f, n, a) < 0);

    wire_peer_t r;
    wire_peer_init(&r, kid, PEER_UP, 0);
    wire_peer_take(&r, &a->carried[0]);
    CHECK(r.ncmd == 2 && r.cmd[0].id == 9 && strcmp(r.cmd[0].line, "#HELLO") == 0 && r.synced);
    wire_data_t d = {.session = 1, .seq = 1, .flags = WD_CMD_ACK, .cmd_ack = 9};
    wire_peer_data(&r, &d, -40, 5);
    CHECK(r.ncmd == 1 && r.cmd[0].id == 10);
    free(a);
}

static void test_offers(void)
{
    uint8_t f[64], hub[6];
    wire_offer_t o = {.hub = {9, 8, 7, 6, 5, 4}, .hops = 2, .rssi = -71, .next_ms = 700, .tag = "12345678"}, g;
    int n = wire_offer_put(f, sizeof(f), &o);
    CHECK(n == 21 && wire_type(f, n) == WIRE_OFFER && wire_offer_get(f, n, &g) == 0);
    CHECK(memcmp(g.hub, o.hub, 6) == 0 && g.hops == 2 && g.rssi == -71 && g.next_ms == 700 &&
          memcmp(g.tag, "12345678", 8) == 0);
    CHECK(wire_offer_get(f, n - 1, &g) < 0);
    f[9] = WIRE_HOPS;
    CHECK(wire_offer_get(f, n, &g) < 0);
    f[9] = 0;
    CHECK(wire_offer_get(f, n, &g) < 0);
    n = wire_ask_put(f, sizeof(f), o.hub);
    CHECK(n == 9 && wire_ask_get(f, n, hub) == 0 && memcmp(hub, o.hub, 6) == 0);
    CHECK(wire_ask_get(f, 8, hub) < 0);
    uint8_t msg[32];
    size_t len = 0;
    wire_offer_signed(&o, node_mac, msg, &len);
    CHECK(len == 21 && memcmp(msg, "qm-offer", 8) == 0 && memcmp(msg + 8, node_mac, 6) == 0 && msg[20] == 2);
}

static void test_carried_lines(void)
{
    char b[PROTO_LINE_MAX + 1], c[PROTO_LINE_MAX + 1], inner[PROTO_LINE_MAX + 1], id[13];
    const uint8_t relay[6] = {0x10, 0x20, 0x30, 0x40, 0x50, 0x60}, hubmac[6] = {1, 1, 1, 1, 1, 1};
    uint8_t who[6];
    const char *w = "@W,9,1740,A4:2B:B0:11:22:33,-61,6,2437,3,4,4,1c,486f6d65";
    int n = wire_carry(b, sizeof(b), node_mac, w, strlen(w), 100, 0, who);
    SAME(b, "@R,a0b1c2d3e4f5,@W,9,1840,A4:2B:B0:11:22:33,-61,6,2437,3,4,4,1c,486f6d65");
    CHECK(n == (int)strlen(b) && memcmp(who, node_mac, 6) == 0);
    n = wire_carry(c, sizeof(c), relay, b, n, 60, 0, who);
    SAME(c, "@R,a0b1c2d3e4f5,@W,9,1900,A4:2B:B0:11:22:33,-61,6,2437,3,4,4,1c,486f6d65");
    CHECK(memcmp(who, node_mac, 6) == 0);
    n = wire_carry(b, sizeof(b), hubmac, c, n, 40, 1, who);
    CHECK(n > 0 && wire_unrelay(b, n, id, inner, sizeof(inner)) > 0 && strcmp(id, "a0b1c2d3e4f5") == 0);
    SAME(inner, "@W,9,1940,A4:2B:B0:11:22:33,-61,6,2437,3,4,4,1c,486f6d65*71\n");
    CHECK(wire_carry(b, sizeof(b), relay, "@R,A0B1C2D3E4F5,@W,1,2", 22, 0, 0, who) < 0);
    CHECK(wire_carry(b, sizeof(b), relay, "@R,a0b1c2d3e4f5,@R,1", 20, 0, 0, who) < 0);
    CHECK(wire_carry(b, sizeof(b), relay, "@R,a0b1c2d3e4f5,W,1", 19, 0, 0, who) < 0);
    const char *h = "@H,1,a0b1c2d3e4f5,1.2.0,0.4,4,0,c3,wifi24+ble+espnow,US,0,idle";
    n = wire_carry(b, sizeof(b), relay, h, strlen(h), 999, 0, who);
    CHECK(n > 0 && strcmp(b + 16, h) == 0 && memcmp(who, relay, 6) == 0);

    char longest[WIRE_LINE_MAX];
    memset(longest, 'f', sizeof(longest));
    memcpy(longest, "@B,4294967295,4294967295,", 25);
    size_t ln = WIRE_LINE_MAX - 4;
    n = wire_carry(b, sizeof(b), node_mac, longest, ln, 4294967295u, 0, who);
    CHECK(n > 0 && n <= WIRE_CARRY_MAX);
    n = wire_carry(c, sizeof(c), relay, b, n, 5, 0, who);
    CHECK(n > 0 && n <= WIRE_CARRY_MAX);
    n = wire_carry(b, sizeof(b), hubmac, c, n, 5, 1, who);
    CHECK(n > 0 && n <= PROTO_LINE_MAX);
}

static void test_via_lines(void)
{
    char b[PROTO_LINE_MAX + 1];
    wire_via_t v = {.id = {0xa0, 0xb1, 0xc2, 0xd3, 0xe4, 0xf5}, .ago = 1200, .rssi = -77, .frames = 30, .resent = 2,
                    .has_ack = 1, .cmd_ack = 12}, g;
    int n = wire_via_line(b, sizeof(b), &v);
    SAME(b, "@V,a0b1c2d3e4f5,1200,-77,30,2,12");
    CHECK(wire_via_get(b, n, &g) == 0 && memcmp(g.id, v.id, 6) == 0 && g.ago == 1200 && g.rssi == -77 &&
          g.frames == 30 && g.resent == 2 && g.has_ack && g.cmd_ack == 12);
    v.has_ack = 0;
    n = wire_via_line(b, sizeof(b), &v);
    SAME(b, "@V,a0b1c2d3e4f5,1200,-77,30,2,");
    CHECK(wire_via_get(b, n, &g) == 0 && !g.has_ack);
    CHECK(wire_via_get("@V,a0b1c2d3e4f5,1,-1,1", 22, &g) < 0);
    CHECK(wire_via_get("@V,a0b1c2d3e4f5,1,-200,1,1,", 27, &g) < 0);
    CHECK(wire_via_get("@V,a0b1c2d3e4fz,1,-1,1,1,", 25, &g) < 0);
    CHECK(wire_via_get("@V,a0b1c2d3e4f5,1,-1,1,1,300", 28, &g) < 0);

    wire_peer_t p;
    const uint8_t relay[6] = {0x10, 0x20, 0x30, 0x40, 0x50, 0x60};
    wire_peer_init(&p, v.id, PEER_NEW, 0);
    wire_peer_cmd(&p, "#HELLO", 6);
    CHECK(!wire_peer_up(&p, 100));
    v.has_ack = 1;
    v.cmd_ack = 40;
    wire_peer_via(&p, relay, &v, 5000);
    CHECK(p.state == PEER_UP && p.has_via && p.via_heard == 3800 && p.cmd[0].id == 41 && p.ncmd == 1);
    CHECK(wire_peer_up(&p, 3800 + WIRE_SILENCE_MS - 1) && !wire_peer_up(&p, 3800 + WIRE_SILENCE_MS));
    v.cmd_ack = 41;
    wire_peer_via(&p, relay, &v, 6000);
    CHECK(p.ncmd == 0);
    wire_node_t nd;
    wire_peer_node(&p, 6000, &nd);
    CHECK(nd.heard_ok && nd.has_via && nd.hops == 2 && nd.ago == 1200 && memcmp(nd.via, relay, 6) == 0);
}

static void test_relay_timing(void)
{
    wire_offer_t near = {.hops = 1, .rssi = -60}, far = {.hops = 2, .rssi = -40};
    CHECK(wire_offer_better(&near, -80, &far, -30));
    CHECK(!wire_offer_better(&far, -30, &near, -80));
    wire_offer_t weak = {.hops = 1, .rssi = -88};
    CHECK(wire_offer_better(&near, -70, &weak, -50));
    CHECK(!wire_offer_better(&near, -70, &near, -70));

    wire_window_t w = {0};
    CHECK(wire_window_next(&w, 100) == 0);
    wire_window_mark(&w, 1000);
    CHECK(wire_window_next(&w, 1100) == 0);
    wire_window_mark(&w, 1800);
    CHECK(w.period == 800 && wire_window_next(&w, 1900) == 700);
    wire_window_mark(&w, 2400);
    CHECK(w.period == 750 && wire_window_next(&w, 3500) == 0);
    wire_window_mark(&w, 9000);
    CHECK(w.period == 750);

    CHECK(wire_relay_budget(1000, 1, 2000, 900) == 0);
    CHECK(wire_relay_budget(1000, 1, 1200, 900) == 200 + WIRE_LATE_MS);
    CHECK(wire_relay_budget(1000, 1, 900, 900) == WIRE_EARLY_MS + WIRE_LATE_MS);
    CHECK(wire_relay_budget(1000, 0, 0, 900) == WIRE_EARLY_MS + WIRE_LATE_MS);
    CHECK(wire_relay_budget(10000, 1, 12000, 1000) == 2000 + WIRE_LATE_MS);
}

static void test_enow_frames(void)
{
    uint8_t f[ENOW_MSG_LEN + 4];
    enow_msg_t m;
    CHECK(enow_put(f, sizeof(f), ENOW_TEXT, 7, "AA:BB:CC:DD:EE:FF,Home,[WPA2_PSK],6,-61,W") == ENOW_MSG_LEN);
    CHECK(enow_get(f, ENOW_MSG_LEN, &m) == 0);
    CHECK(m.type == ENOW_TEXT && m.counter == 7 && m.len == 41);
    SAME(m.text, "AA:BB:CC:DD:EE:FF,Home,[WPA2_PSK],6,-61,W");
    CHECK(enow_get(f, 4, &m) < 0);
    f[0] = 'Q';
    CHECK(enow_get(f, ENOW_MSG_LEN, &m) < 0);
    CHECK(enow_put(f, 100, ENOW_TEXT, 0, "x") < 0);
    enow_put(f, sizeof(f), ENOW_TEXT, 0, "abc");
    f[9] = 201;
    CHECK(enow_get(f, ENOW_MSG_LEN, &m) < 0);
    f[9] = 3;
    CHECK(enow_get(f, 13, &m) < 0);
    CHECK(enow_get(f, 14, &m) == 0);

    enow_admin_t a = {.version = 9, .index = 1, .count = 3, .start = 14, .end = 39}, b;
    CHECK(enow_admin_put(f, sizeof(f), &a) == ENOW_ADMIN_LEN);
    CHECK(memcmp(f, "ENOW\x05\x09\x01\x03\x0e\x27", 10) == 0);
    CHECK(enow_admin_get(f, ENOW_ADMIN_LEN, &b) == 0);
    CHECK(b.version == 9 && b.index == 1 && b.count == 3 && b.start == 14 && b.end == 39);
    CHECK(enow_get(f, ENOW_ADMIN_LEN, &m) == 0 && m.type == ENOW_ADMIN);

    enow_put(f, sizeof(f), ENOW_CORE_REQUEST, 0, "PIGLET1\x02" "1.4.0");
    enow_get(f, ENOW_MSG_LEN, &m);
    CHECK(enow_kind(&m) == ENOW_KIND_PIGLET);
    enow_put(f, sizeof(f), ENOW_CORE_REQUEST, 0, "C5P1;ROLE=EXTERNAL;BANDS=6");
    enow_get(f, ENOW_MSG_LEN, &m);
    CHECK(enow_kind(&m) == ENOW_KIND_6G);
    enow_put(f, sizeof(f), ENOW_CORE_REQUEST, 0, "");
    enow_get(f, ENOW_MSG_LEN, &m);
    CHECK(enow_kind(&m) == ENOW_KIND_JCMK);
}

static void test_enow_records(void)
{
    enow_rec_t r;
    char out[PROTO_LINE_MAX + 1];
    const char *t = "a4:2b:b0:11:22:33,Home,[WPA2_PSK],6,-61,W";
    CHECK(enow_record(t, strlen(t), &r) == ENOW_REC_WIFI);
    CHECK(r.wifi.channel == 6 && r.freq == 2437 && r.wifi.rssi == -61 && r.wifi.auth == 3 && r.wifi.pairwise == 4);
    enow_wifi_line(out, sizeof(out), 9, 0, &r);
    SAME(out, "@W,9,0,A4:2B:B0:11:22:33,-61,6,2437,3,4,4,0,486f6d65*2F\n");

    t = "A4:2B:B0:11:22:34,,WPA2EAP,149,-80,W";
    CHECK(enow_record(t, strlen(t), &r) == ENOW_REC_WIFI);
    CHECK(r.freq == 5745 && r.wifi.auth == 5 && (r.wifi.flags & WF_HIDDEN));
    t = "A4:2B:B0:11:22:35,Six,[WPA3_PSK],37,-70,W6";
    CHECK(enow_record(t, strlen(t), &r) == ENOW_REC_WIFI);
    CHECK(r.freq == 6135 && r.wifi.auth == 6);
    enow_wifi_line(out, sizeof(out), 1, 0, &r);
    CHECK(strstr(out, ",37,6135,6,") != NULL);
    t = "A4:2B:B0:11:22:36,Odd,[NEW],1,-50,W";
    CHECK(enow_record(t, strlen(t), &r) == ENOW_REC_WIFI && r.wifi.auth == 0 && r.wifi.pairwise == 12);
    t = "A4:2B:B0:11:22:37,Ssid,with,comma,[OPEN],1,-50,W";
    CHECK(enow_record(t, strlen(t), &r) < 0);

    t = "11:22:33:44:55:66,,[BLE],0,-72,B";
    CHECK(enow_record(t, strlen(t), &r) == ENOW_REC_BLE);
    proto_ble(out, sizeof(out), 4, 0, &r.ble);
    CHECK(strncmp(out, "@B,4,0,11:22:33:44:55:66,0,-72,0,,*", 35) == 0);

    CHECK(enow_record("x", 1, &r) < 0);
    t = "A4:2B:B0:11:22:33,Home,[OPEN],6,-61,X";
    CHECK(enow_record(t, strlen(t), &r) < 0);
    t = "A4:2B:B0:11:22,Home,[OPEN],6,-61,W";
    CHECK(enow_record(t, strlen(t), &r) < 0);
    t = "A4:2B:B0:11:22:33,Home,[OPEN],six,-61,W";
    CHECK(enow_record(t, strlen(t), &r) < 0);
    t = "A4:2B:B0:11:22:33,Home,[OPEN],15,-61,W";
    CHECK(enow_record(t, strlen(t), &r) < 0);
}

static void test_enow_ranges(void)
{
    uint8_t lo, hi;
    CHECK(enow_range(0x7ffe, 0x1ffffffe, &lo, &hi) == 0 && lo == 0 && hi == 39);
    CHECK(enow_range(1 << 1 | 1 << 6 | 1 << 11, 1, &lo, &hi) == 0 && lo == 0 && hi == 10);
    CHECK(enow_range(1, 1u << 21 | 1u << 28, &lo, &hi) == 0 && enow_channels[lo] == 149 && enow_channels[hi] == 177);
    CHECK(enow_range(1, 1u << 1 | 1u << 8, &lo, &hi) == 0 && enow_channels[lo] == 36 && enow_channels[hi] == 64);
    CHECK(enow_range(1, 1u << 9 | 1u << 20, &lo, &hi) == 0 && enow_channels[lo] == 100 && enow_channels[hi] == 144);
    CHECK(enow_range(1, 1u << 10 | 1u << 11, &lo, &hi) < 0);
    CHECK(enow_range(1, 1, &lo, &hi) < 0);
}

int main(void)
{
    test_checksum();
    test_lines();
    test_parse();
    test_plan();
    test_split();
    test_scan_time();
    test_led_and_ident();
    test_adv();
    test_seen();
    test_seen_floor_keeps_the_far_edge();
    test_seen_reports_the_first_manufacturer();
    test_frames();
    test_outbox();
    test_outbox_waits_while_the_hub_is_out_of_reach();
    test_a_busy_hub_keeps_the_lines_on_the_node();
    test_peer_and_commands();
    test_commands_after_the_hub_starts_again_reach_the_node();
    test_a_late_send_report_is_not_taken_for_the_next_send();
    test_the_button_counts_only_after_it_was_seen_up();
    test_pairing();
    test_a_second_key_under_one_node_id_is_refused();
    test_air_states();
    test_chunks();
    test_hub_commands();
    test_relay();
    test_hub_lines();
    test_relayed_answers();
    test_offers();
    test_carried_lines();
    test_via_lines();
    test_relay_timing();
    test_enow_frames();
    test_enow_records();
    test_enow_ranges();
    printf("%d checks, %d failed\n", checks, failures);
    return failures != 0;
}
