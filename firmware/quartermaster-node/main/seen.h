#pragma once

#include <stdint.h>

#define SEEN_REFRESH_MS 30000u
#define SEEN_KEY 8

typedef struct {
    uint8_t key[SEEN_KEY];
    uint32_t at;
    int8_t hi, lo;
    uint8_t info;
    uint8_t pad;
} seen_entry_t;

typedef struct {
    seen_entry_t *e;
    uint16_t *next;
    uint16_t *head;
    unsigned cap, mask, count;
    uint32_t added;
} seen_t;

typedef enum { SEEN_SKIP, SEEN_NEW, SEEN_AGAIN } seen_result_t;

int seen_init(seen_t *t, unsigned cap);
void seen_free(seen_t *t);
void seen_clear(seen_t *t);
seen_result_t seen_check(const seen_t *t, const uint8_t key[SEEN_KEY], int8_t rssi, uint32_t now, uint8_t info);
seen_result_t seen_note(seen_t *t, const uint8_t key[SEEN_KEY], int8_t rssi, uint32_t now, uint8_t info);
