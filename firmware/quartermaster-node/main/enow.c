#include "enow.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

const uint8_t enow_channels[ENOW_CHANNELS] = {1,   2,   3,   4,   5,   6,   7,   8,   9,   10,  11,  12,  13,  14,
                                              36,  40,  44,  48,  52,  56,  60,  64,  100, 112, 116, 120, 124, 128,
                                              132, 136, 140, 144, 149, 153, 157, 161, 165, 169, 173, 177};

static const char magic[4] = {'E', 'N', 'O', 'W'};

int enow_get(const uint8_t *d, size_t n, enow_msg_t *m)
{
    if (n < 5 || memcmp(d, magic, 4)) {
        return -1;
    }
    memset(m, 0, sizeof(*m));
    m->type = d[4];
    if (m->type == ENOW_ADMIN) {
        return n >= ENOW_ADMIN_LEN ? 0 : -1;
    }
    if (n < 11) {
        return -1;
    }
    m->counter = d[5] | d[6] << 8 | d[7] << 16 | (uint32_t)d[8] << 24;
    m->len = d[9] | d[10] << 8;
    size_t room = n - 11;
    if (m->len > ENOW_TEXT_MAX || m->len > room) {
        return -1;
    }
    memcpy(m->text, d + 11, m->len);
    m->text[m->len] = 0;
    return 0;
}

int enow_put(uint8_t *out, size_t cap, uint8_t type, uint32_t counter, const char *text)
{
    size_t len = text ? strlen(text) : 0;
    if (cap < ENOW_MSG_LEN || len > ENOW_TEXT_MAX) {
        return -1;
    }
    memset(out, 0, ENOW_MSG_LEN);
    memcpy(out, magic, 4);
    out[4] = type;
    out[5] = counter;
    out[6] = counter >> 8;
    out[7] = counter >> 16;
    out[8] = counter >> 24;
    out[9] = len;
    out[10] = len >> 8;
    if (len) {
        memcpy(out + 11, text, len);
    }
    return ENOW_MSG_LEN;
}

int enow_admin_put(uint8_t *out, size_t cap, const enow_admin_t *a)
{
    if (cap < ENOW_ADMIN_LEN) {
        return -1;
    }
    memcpy(out, magic, 4);
    out[4] = ENOW_ADMIN;
    out[5] = a->version;
    out[6] = a->index;
    out[7] = a->count;
    out[8] = a->start;
    out[9] = a->end;
    return ENOW_ADMIN_LEN;
}

int enow_admin_get(const uint8_t *d, size_t n, enow_admin_t *a)
{
    if (n < ENOW_ADMIN_LEN || memcmp(d, magic, 4) || d[4] != ENOW_ADMIN) {
        return -1;
    }
    a->version = d[5];
    a->index = d[6];
    a->count = d[7];
    a->start = d[8];
    a->end = d[9];
    return 0;
}

int enow_kind(const enow_msg_t *m)
{
    static const char six[] = "C5P1;ROLE=EXTERNAL;BANDS=6";
    if (m->len >= 7 && memcmp(m->text, "PIGLET1", 7) == 0) {
        return ENOW_KIND_PIGLET;
    }
    if (m->len == sizeof(six) - 1 && memcmp(m->text, six, sizeof(six) - 1) == 0) {
        return ENOW_KIND_6G;
    }
    return ENOW_KIND_JCMK;
}

typedef struct {
    const char *name;
    uint8_t auth, pairwise;
} auth_t;

static const auth_t auths[] = {
    {"[OPEN]", 0, 0},         {"OPEN", 0, 0},
    {"[WEP]", 1, 1},          {"WEP", 1, 1},
    {"[WPA_PSK]", 2, 3},      {"WPA", 2, 3},
    {"[WPA2_PSK]", 3, 4},     {"WPA2", 3, 4},
    {"[WPA_WPA2_PSK]", 4, 5}, {"WPAWPA2", 4, 5},
    {"[WPA2]", 5, 4},         {"[WPA2_ENTERPRISE]", 5, 4},
    {"WPA2EAP", 5, 4},        {"[WPA3_PSK]", 6, 4},
    {"WPA3", 6, 4},           {"[WPA2_WPA3_PSK]", 7, 4},
    {"WPA2WPA3", 7, 4},       {"[WAPI_PSK]", 8, 7},
};

int enow_auth(const char *s, size_t n, uint8_t *auth, uint8_t *pairwise)
{
    for (size_t i = 0; i < sizeof(auths) / sizeof(auths[0]); i++) {
        if (strlen(auths[i].name) == n && memcmp(auths[i].name, s, n) == 0) {
            *auth = auths[i].auth;
            *pairwise = auths[i].pairwise;
            return 0;
        }
    }
    *auth = 0;
    *pairwise = 12;
    return -1;
}

static int mac_get(const char *s, size_t n, uint8_t out[6])
{
    if (n != 17) {
        return -1;
    }
    for (int i = 0; i < 6; i++) {
        char hex[3] = {s[i * 3], s[i * 3 + 1], 0};
        char *end;
        if (i < 5 && s[i * 3 + 2] != ':') {
            return -1;
        }
        for (int k = 0; k < 2; k++) {
            char c = hex[k];
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'))) {
                return -1;
            }
        }
        out[i] = strtoul(hex, &end, 16);
    }
    return 0;
}

static int int_get(const char *s, size_t n, long lo, long hi, long *v)
{
    char buf[12];
    char *end;
    if (n == 0 || n >= sizeof(buf)) {
        return -1;
    }
    memcpy(buf, s, n);
    buf[n] = 0;
    *v = strtol(buf, &end, 10);
    return *end || *v < lo || *v > hi ? -1 : 0;
}

static unsigned freq_6g(unsigned ch)
{
    if (ch == 2) {
        return 5935;
    }
    return ch >= 1 && ch <= 233 ? 5950 + 5 * ch : 0;
}

int enow_record(const char *text, size_t n, enow_rec_t *r)
{
    const char *f[6];
    size_t len[6];
    size_t k = 0, start = 0;
    for (size_t i = 0; i <= n && k < 6; i++) {
        if (i == n || text[i] == ',') {
            f[k] = text + start;
            len[k++] = i - start;
            start = i + 1;
            if (k == 6 && i != n) {
                len[5] = n - (f[5] - text);
                break;
            }
        }
    }
    if (k != 6) {
        return -1;
    }
    long channel, rssi;
    memset(r, 0, sizeof(*r));
    if (int_get(f[3], len[3], 0, 255, &channel) || int_get(f[4], len[4], -128, 127, &rssi)) {
        return -1;
    }
    if (len[5] == 1 && f[5][0] == 'B') {
        proto_ble_t *b = &r->ble;
        uint8_t a[6];
        if (mac_get(f[0], len[0], a)) {
            return -1;
        }
        for (int i = 0; i < 6; i++) {
            b->addr[i] = a[5 - i];
        }
        b->rssi = rssi;
        b->name_len = len[1] > PROTO_NAME_MAX ? PROTO_NAME_MAX : len[1];
        memcpy(b->name, f[1], b->name_len);
        r->kind = ENOW_REC_BLE;
        return r->kind;
    }
    int six = len[5] == 2 && f[5][0] == 'W' && f[5][1] == '6';
    if (!six && !(len[5] == 1 && f[5][0] == 'W')) {
        return -1;
    }
    proto_wifi_t *w = &r->wifi;
    if (mac_get(f[0], len[0], w->bssid)) {
        return -1;
    }
    w->ssid_len = len[1] > 32 ? 32 : len[1];
    memcpy(w->ssid, f[1], w->ssid_len);
    w->rssi = rssi;
    w->channel = channel;
    r->freq = six ? freq_6g(channel) : proto_freq(channel);
    if (!r->freq) {
        return -1;
    }
    enow_auth(f[2], len[2], &w->auth, &w->pairwise);
    w->group = w->pairwise;
    if (!w->ssid_len) {
        w->flags |= WF_HIDDEN;
    }
    r->kind = ENOW_REC_WIFI;
    return r->kind;
}

int enow_wifi_line(char *out, size_t cap, uint32_t seq, uint32_t age, const enow_rec_t *r)
{
    const proto_wifi_t *w = &r->wifi;
    const uint8_t *b = w->bssid;
    int n = proto_put(out, cap, 0, "@W,%lu,%lu,%02X:%02X:%02X:%02X:%02X:%02X,%d,%u,%u,%u,%u,%u,%x,",
                      (unsigned long)seq, (unsigned long)age, b[0], b[1], b[2], b[3], b[4], b[5], w->rssi,
                      w->channel, r->freq, w->auth, w->pairwise, w->group, w->flags);
    for (unsigned i = 0; i < w->ssid_len && n >= 0; i++) {
        n = proto_put(out, cap, n, "%02x", w->ssid[i]);
    }
    return proto_end(out, cap, n);
}

int enow_range(uint16_t ch2, uint32_t ch5, uint8_t *lo, uint8_t *hi)
{
    int first = -1, last = -1;
    for (int i = 0; i < ENOW_CHANNELS; i++) {
        unsigned ch = enow_channels[i];
        int on = 0;
        if (ch <= 14) {
            on = (ch2 & 1) == 0 && (ch2 >> ch & 1);
        } else {
            unsigned bit = ch <= 64 ? (ch - 32) / 4 : ch <= 144 ? (ch - 100) / 4 + 9 : (ch - 149) / 4 + 21;
            on = (ch5 & 1) == 0 && (ch5 >> bit & 1);
        }
        if (on) {
            first = first < 0 ? i : first;
            last = i;
        }
    }
    if (first < 0) {
        return -1;
    }
    *lo = first;
    *hi = last;
    return 0;
}
