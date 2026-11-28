#pragma once

#include <stddef.h>
#include <stdint.h>

#include "proto.h"

#define ENOW_TEXT_MAX 200
#define ENOW_MSG_LEN 212
#define ENOW_ADMIN_LEN 10
#define ENOW_CHANNEL 6
#define ENOW_CHANNELS 40

enum { ENOW_CORE_REQUEST = 1, ENOW_CORE_REPLY, ENOW_HEARTBEAT, ENOW_TEXT, ENOW_ADMIN, ENOW_RECORD_ACK };

enum { ENOW_KIND_JCMK, ENOW_KIND_PIGLET, ENOW_KIND_6G };

enum { ENOW_REC_WIFI = 1, ENOW_REC_BLE };

typedef struct {
    uint8_t type;
    uint32_t counter;
    uint16_t len;
    char text[ENOW_TEXT_MAX + 1];
} enow_msg_t;

typedef struct {
    uint8_t version, index, count, start, end;
} enow_admin_t;

typedef struct {
    int kind;
    proto_wifi_t wifi;
    unsigned freq;
    proto_ble_t ble;
} enow_rec_t;

extern const uint8_t enow_channels[ENOW_CHANNELS];

int enow_get(const uint8_t *d, size_t n, enow_msg_t *m);
int enow_put(uint8_t *out, size_t cap, uint8_t type, uint32_t counter, const char *text);
int enow_admin_put(uint8_t *out, size_t cap, const enow_admin_t *a);
int enow_admin_get(const uint8_t *d, size_t n, enow_admin_t *a);
int enow_kind(const enow_msg_t *m);
int enow_auth(const char *s, size_t n, uint8_t *auth, uint8_t *pairwise);
int enow_record(const char *text, size_t n, enow_rec_t *r);
int enow_wifi_line(char *out, size_t cap, uint32_t seq, uint32_t age, const enow_rec_t *r);
int enow_range(uint16_t ch2, uint32_t ch5, uint8_t *lo, uint8_t *hi);
