#pragma once

#include <stddef.h>
#include <stdint.h>

#define PROTO_VERSION 1
#define PROTO_LINE_MAX 240
#define PROTO_NAME_MAX 64
#define PROTO_TEXT_MAX 96
#define PROTO_ARGS_MAX 10

#define PROTO_2G_ALL 0x7ffeu
#define PROTO_5G_ALL 0x1ffffffeu
#define PROTO_2G_PASSIVE 0x7000u
#define PROTO_5G_PASSIVE 0x1c1fffe0u

enum {
    WF_HIDDEN = 1 << 0,
    WF_WPS = 1 << 1,
    WF_11B = 1 << 2,
    WF_11G = 1 << 3,
    WF_11N = 1 << 4,
    WF_11A = 1 << 5,
    WF_11AC = 1 << 6,
    WF_11AX = 1 << 7,
    WF_FTM = 1 << 8,
};

enum {
    BF_CONN = 1 << 0,
    BF_SCAN = 1 << 1,
    BF_RSP = 1 << 2,
};

typedef struct {
    uint8_t bssid[6];
    uint8_t ssid[32];
    uint8_t ssid_len;
    int8_t rssi;
    uint8_t channel;
    uint8_t auth;
    uint8_t pairwise;
    uint8_t group;
    uint16_t flags;
} proto_wifi_t;

typedef struct {
    uint8_t addr[6];
    uint8_t addr_type;
    int8_t rssi;
    uint8_t flags;
    uint8_t has_mfr;
    uint16_t mfr;
    uint8_t name_len;
    uint8_t name[PROTO_NAME_MAX];
} proto_ble_t;

typedef struct {
    char id[13];
    const char *fw;
    unsigned rev_major, rev_minor;
    unsigned flash_mb, psram_mb;
    const char *board;
    const char *caps;
    char country[3];
    uint32_t plan;
    int running;
} proto_hello_t;

typedef struct {
    uint32_t uptime, sweeps, last_sweep, networks, devices, dropped, plan, heap;
    int running;
} proto_status_t;

typedef struct {
    uint32_t version;
    char country[3];
    uint16_t ch2;
    uint32_t ch5;
    uint16_t active_min, active_max, passive;
    uint8_t ble;
} proto_plan_t;

typedef enum { LED_NONE, LED_GPIO, LED_WS2812 } led_kind_t;

typedef struct {
    led_kind_t kind;
    uint8_t gpio;
    uint8_t active_low;
} proto_led_t;

typedef enum { PROTO_CMD, PROTO_IGNORE, PROTO_BADSUM } proto_parse_t;

typedef struct {
    char buf[PROTO_LINE_MAX + 1];
    char *name;
    char *argv[PROTO_ARGS_MAX];
    int argc;
} proto_cmd_t;

typedef struct {
    const uint8_t *name;
    uint8_t name_len;
    uint8_t has_mfr;
    uint16_t mfr;
} proto_adv_t;

uint8_t proto_xor(const char *s, size_t n);
unsigned proto_freq(unsigned channel);

int proto_hello(char *out, size_t cap, const proto_hello_t *h);
int proto_wifi(char *out, size_t cap, uint32_t seq, uint32_t age, const proto_wifi_t *w);
int proto_ble(char *out, size_t cap, uint32_t seq, uint32_t age, const proto_ble_t *b);
int proto_status(char *out, size_t cap, uint32_t seq, const proto_status_t *s);
int proto_ack(char *out, size_t cap, uint32_t seq, const char *cmd, int ok, const char *detail);
int proto_log(char *out, size_t cap, uint32_t seq, const char *text);

proto_parse_t proto_parse(const char *line, size_t n, proto_cmd_t *c);
int proto_country_ok(const char *cc);
const char *proto_plan(const proto_cmd_t *c, proto_plan_t *p);
const char *proto_led(const proto_cmd_t *c, proto_led_t *l);
const char *proto_ident(const proto_cmd_t *c, unsigned *seconds);
void proto_default_plan(proto_plan_t *p, const char *country);
void proto_split(const proto_plan_t *p, uint16_t *a2, uint32_t *a5, uint16_t *p2, uint32_t *p5);
uint32_t proto_scan_ms(uint16_t c2, uint32_t c5, unsigned dwell);
void proto_adv_parse(const uint8_t *d, size_t n, proto_adv_t *a);
