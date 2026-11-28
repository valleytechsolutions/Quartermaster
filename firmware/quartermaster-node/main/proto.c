#include "proto.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *const countries[] = {
    "01", "AT", "AU", "BE", "BG", "BR", "CA", "CH", "CN", "CY", "CZ", "DE", "DK", "EE", "ES",
    "FI", "FR", "GB", "GR", "HK", "HR", "HU", "IE", "IN", "IS", "IT", "JP", "KR", "LI", "LT",
    "LU", "LV", "MT", "MX", "NL", "NO", "NZ", "PL", "PT", "RO", "SE", "SI", "SK", "TW", "US",
};

uint8_t proto_xor(const char *s, size_t n)
{
    uint8_t x = 0;
    while (n--) {
        x ^= (uint8_t)*s++;
    }
    return x;
}

unsigned proto_freq(unsigned ch)
{
    if (ch == 14) {
        return 2484;
    }
    if (ch >= 1 && ch <= 13) {
        return 2407 + 5 * ch;
    }
    if (ch >= 32 && ch <= 196) {
        return 5000 + 5 * ch;
    }
    return 0;
}

int proto_end(char *out, size_t cap, int len)
{
    if (len < 0 || (size_t)len + 4 > cap || len + 4 > PROTO_LINE_MAX) {
        return -1;
    }
    snprintf(out + len, cap - len, "*%02X\n", proto_xor(out + 1, len - 1));
    return len + 4;
}

int proto_put(char *out, size_t cap, int len, const char *fmt, ...)
{
    if (len < 0 || (size_t)len >= cap) {
        return -1;
    }
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(out + len, cap - len, fmt, ap);
    va_end(ap);
    if (n < 0 || (size_t)(len + n) >= cap) {
        return -1;
    }
    return len + n;
}

static int hex(char *out, size_t cap, int len, const uint8_t *b, size_t n)
{
    static const char digits[] = "0123456789abcdef";
    if (len < 0 || (size_t)len + 2 * n + 1 > cap) {
        return -1;
    }
    for (size_t i = 0; i < n; i++) {
        out[len++] = digits[b[i] >> 4];
        out[len++] = digits[b[i] & 15];
    }
    out[len] = 0;
    return len;
}

int proto_text(char *out, size_t cap, int len, const char *s, size_t max)
{
    if (len < 0) {
        return -1;
    }
    for (size_t i = 0; s && s[i] && i < max; i++) {
        if ((size_t)len + 2 > cap) {
            return -1;
        }
        char c = s[i];
        out[len++] = (c < 0x20 || c > 0x7e || c == ',' || c == '*') ? '_' : c;
    }
    out[len] = 0;
    return len;
}

static int mac(char *out, size_t cap, int len, const uint8_t *m, int reverse)
{
    uint8_t b[6];
    for (int i = 0; i < 6; i++) {
        b[i] = m[reverse ? 5 - i : i];
    }
    return proto_put(out, cap, len, "%02X:%02X:%02X:%02X:%02X:%02X", b[0], b[1], b[2], b[3], b[4], b[5]);
}

int proto_hello(char *out, size_t cap, const proto_hello_t *h)
{
    int n = proto_put(out, cap, 0, "@H,%d,%s,%s,%u.%u,%u,%u,%s,%s,%s,%lu,%s", PROTO_VERSION, h->id, h->fw,
                h->rev_major, h->rev_minor, h->flash_mb, h->psram_mb, h->board, h->caps, h->country,
                (unsigned long)h->plan, h->running ? "run" : "idle");
    return proto_end(out, cap, n);
}

int proto_wifi(char *out, size_t cap, uint32_t seq, uint32_t age, const proto_wifi_t *w)
{
    int n = proto_put(out, cap, 0, "@W,%lu,%lu,", (unsigned long)seq, (unsigned long)age);
    n = mac(out, cap, n, w->bssid, 0);
    n = proto_put(out, cap, n, ",%d,%u,%u,%u,%u,%u,%x,", w->rssi, w->channel, proto_freq(w->channel), w->auth,
            w->pairwise, w->group, w->flags);
    n = hex(out, cap, n, w->ssid, w->ssid_len > 32 ? 32 : w->ssid_len);
    return proto_end(out, cap, n);
}

int proto_ble(char *out, size_t cap, uint32_t seq, uint32_t age, const proto_ble_t *b)
{
    int n = proto_put(out, cap, 0, "@B,%lu,%lu,", (unsigned long)seq, (unsigned long)age);
    n = mac(out, cap, n, b->addr, 1);
    n = proto_put(out, cap, n, ",%u,%d,%x,", b->addr_type, b->rssi, b->flags);
    if (b->has_mfr) {
        n = proto_put(out, cap, n, "%04x", b->mfr);
    }
    n = proto_put(out, cap, n, ",");
    n = hex(out, cap, n, b->name, b->name_len > PROTO_NAME_MAX ? PROTO_NAME_MAX : b->name_len);
    return proto_end(out, cap, n);
}

int proto_status(char *out, size_t cap, uint32_t seq, const proto_status_t *s)
{
    int n = proto_put(out, cap, 0, "@S,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%s", (unsigned long)seq,
                (unsigned long)s->uptime, (unsigned long)s->sweeps, (unsigned long)s->last_sweep,
                (unsigned long)s->networks, (unsigned long)s->devices, (unsigned long)s->dropped,
                (unsigned long)s->plan, (unsigned long)s->heap, s->running ? "run" : "idle");
    return proto_end(out, cap, n);
}

int proto_ack(char *out, size_t cap, uint32_t seq, const char *cmd, int ok, const char *detail)
{
    int n = proto_put(out, cap, 0, "@A,%lu,", (unsigned long)seq);
    n = proto_text(out, cap, n, cmd, 16);
    n = proto_put(out, cap, n, ",%s,", ok ? "ok" : "err");
    n = proto_text(out, cap, n, detail, PROTO_TEXT_MAX);
    return proto_end(out, cap, n);
}

int proto_log(char *out, size_t cap, uint32_t seq, const char *s)
{
    int n = proto_put(out, cap, 0, "@L,%lu,", (unsigned long)seq);
    n = proto_text(out, cap, n, s, PROTO_TEXT_MAX);
    return proto_end(out, cap, n);
}

static int hexval(char c)
{
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

proto_parse_t proto_parse(const char *line, size_t n, proto_cmd_t *c)
{
    while (n && (line[n - 1] == '\r' || line[n - 1] == '\n' || line[n - 1] == ' ')) {
        n--;
    }
    if (n < 2 || line[0] != '#' || n > PROTO_LINE_MAX) {
        return PROTO_IGNORE;
    }
    const char *star = memchr(line, '*', n);
    size_t body = star ? (size_t)(star - line) : n;
    memcpy(c->buf, line + 1, body - 1);
    c->buf[body - 1] = 0;
    c->name = c->buf;
    c->argc = 0;
    char *p = strchr(c->buf, ',');
    while (p) {
        *p++ = 0;
        if (c->argc == PROTO_ARGS_MAX) {
            c->argc++;
            break;
        }
        c->argv[c->argc++] = p;
        p = strchr(p, ',');
    }
    if (star) {
        int hi = n - body == 3 ? hexval(star[1]) : -1, lo = n - body == 3 ? hexval(star[2]) : -1;
        if (hi < 0 || lo < 0 || proto_xor(line + 1, body - 1) != (uint8_t)(hi << 4 | lo)) {
            return PROTO_BADSUM;
        }
    }
    return PROTO_CMD;
}

int proto_country_ok(const char *cc)
{
    for (size_t i = 0; i < sizeof(countries) / sizeof(countries[0]); i++) {
        if (strcmp(cc, countries[i]) == 0) {
            return 1;
        }
    }
    return 0;
}

static int number(const char *s, int base, unsigned long max, unsigned long *v)
{
    if (!s || !*s || strlen(s) > 10) {
        return 0;
    }
    for (const char *q = s; *q; q++) {
        if (base == 10 ? (*q < '0' || *q > '9') : hexval(*q) < 0) {
            return 0;
        }
    }
    *v = strtoul(s, NULL, base);
    return *v <= max;
}

const char *proto_plan(const proto_cmd_t *c, proto_plan_t *p)
{
    unsigned long v, ch2, ch5, amin, amax, pas, ble;
    if (c->argc != 8) {
        return "args";
    }
    if (!number(c->argv[0], 10, 0xffffffffUL, &v) || v == 0) {
        return "version";
    }
    if (strlen(c->argv[1]) != 2 || !proto_country_ok(c->argv[1])) {
        return "country";
    }
    if (!number(c->argv[2], 16, 0x7fff, &ch2) || !number(c->argv[3], 16, 0x1fffffff, &ch5)) {
        return "bitmap";
    }
    if (!number(c->argv[4], 10, 1500, &amin) || !number(c->argv[5], 10, 1500, &amax) ||
        !number(c->argv[6], 10, 1500, &pas) || amax == 0 || amin > amax || pas == 0) {
        return "time";
    }
    if (!number(c->argv[7], 10, 1, &ble)) {
        return "ble";
    }
    p->version = v;
    memcpy(p->country, c->argv[1], 3);
    p->ch2 = ch2;
    p->ch5 = ch5;
    p->active_min = amin;
    p->active_max = amax;
    p->passive = pas;
    p->ble = ble;
    return NULL;
}

static uint64_t led_pins = 0x1f8007ffULL;

void proto_led_pins(uint64_t mask)
{
    led_pins = mask;
}

static int pin_ok(unsigned long g)
{
    return g < 64 && (led_pins >> g & 1);
}

const char *proto_led(const proto_cmd_t *c, proto_led_t *l)
{
    unsigned long g = 0, al = 0;
    if (c->argc < 1 || c->argc > 3) {
        return "args";
    }
    if (strcmp(c->argv[0], "none") == 0) {
        l->kind = LED_NONE;
        l->gpio = 0;
        l->active_low = 0;
        return NULL;
    }
    if (strcmp(c->argv[0], "gpio") && strcmp(c->argv[0], "ws2812")) {
        return "kind";
    }
    if (c->argc < 2 || !number(c->argv[1], 10, 48, &g) || !pin_ok(g)) {
        return "gpio";
    }
    if (c->argc == 3 && *c->argv[2] && !number(c->argv[2], 10, 1, &al)) {
        return "active_low";
    }
    l->kind = strcmp(c->argv[0], "gpio") == 0 ? LED_GPIO : LED_WS2812;
    l->gpio = g;
    l->active_low = l->kind == LED_GPIO ? al : 0;
    return NULL;
}

const char *proto_ident(const proto_cmd_t *c, unsigned *seconds)
{
    unsigned long s = 5;
    if (c->argc > 1 || (c->argc == 1 && (!number(c->argv[0], 10, 60, &s) || s == 0))) {
        return "seconds";
    }
    *seconds = s;
    return NULL;
}

void proto_default_plan(proto_plan_t *p, const char *country)
{
    memset(p, 0, sizeof(*p));
    memcpy(p->country, country, 2);
    p->ch2 = PROTO_2G_ALL;
    p->ch5 = PROTO_5G_ALL;
    p->active_min = 30;
    p->active_max = 80;
    p->passive = 110;
}

void proto_split(const proto_plan_t *p, uint16_t *a2, uint32_t *a5, uint16_t *p2, uint32_t *p5)
{
    uint16_t c2 = (p->ch2 & 1) ? 0 : p->ch2 & PROTO_2G_ALL;
    uint32_t c5 = (p->ch5 & 1) ? 0 : p->ch5 & PROTO_5G_ALL;
    *a2 = c2 & ~PROTO_2G_PASSIVE;
    *a5 = c5 & ~PROTO_5G_PASSIVE;
    *p2 = c2 & PROTO_2G_PASSIVE;
    *p5 = c5 & PROTO_5G_PASSIVE;
}

uint32_t proto_scan_ms(uint16_t c2, uint32_t c5, unsigned dwell)
{
    unsigned n = __builtin_popcount(c2 & PROTO_2G_ALL) + __builtin_popcount(c5 & PROTO_5G_ALL);
    return n * (dwell + 50u) + 2000u;
}

void proto_adv_parse(const uint8_t *d, size_t n, proto_adv_t *a)
{
    int complete = 0;
    memset(a, 0, sizeof(*a));
    size_t i = 0;
    while (i < n) {
        uint8_t len = d[i];
        if (len == 0 || i + 1 + len > n) {
            break;
        }
        uint8_t type = d[i + 1];
        const uint8_t *v = d + i + 2;
        uint8_t vl = len - 1;
        if ((type == 0x09 || (type == 0x08 && !complete && !a->name)) && vl) {
            a->name = v;
            a->name_len = vl > PROTO_NAME_MAX ? PROTO_NAME_MAX : vl;
            complete = type == 0x09;
        } else if (type == 0xff && vl >= 2 && !a->has_mfr) {
            a->has_mfr = 1;
            a->mfr = v[0] | v[1] << 8;
        }
        i += 1 + len;
    }
}
