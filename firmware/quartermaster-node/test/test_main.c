#include <stdio.h>
#include <string.h>

#include "proto.h"
#include "seen.h"

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
    printf("%d checks, %d failed\n", checks, failures);
    return failures != 0;
}
