#include "seen.h"

#include <stdlib.h>
#include <string.h>

#define NIL 0xffffu

static unsigned hash(const seen_t *t, const uint8_t *k)
{
    uint32_t a = k[0] | k[1] << 8 | k[2] << 16 | (uint32_t)k[3] << 24;
    uint32_t b = k[4] | k[5] << 8 | k[6] << 16 | (uint32_t)k[7] << 24;
    uint32_t h = a * 0x9e3779b1u ^ b * 0x85ebca77u;
    return (h ^ h >> 15) & t->mask;
}

int seen_init(seen_t *t, unsigned cap)
{
    unsigned nb = 1;
    memset(t, 0, sizeof(*t));
    if (cap == 0 || cap >= NIL) {
        return -1;
    }
    while (nb < cap) {
        nb <<= 1;
    }
    t->e = malloc(cap * sizeof(*t->e));
    t->next = malloc(cap * sizeof(*t->next));
    t->head = malloc(nb * sizeof(*t->head));
    if (!t->e || !t->next || !t->head) {
        seen_free(t);
        return -1;
    }
    t->cap = cap;
    t->mask = nb - 1;
    seen_clear(t);
    return 0;
}

void seen_free(seen_t *t)
{
    free(t->e);
    free(t->next);
    free(t->head);
    memset(t, 0, sizeof(*t));
}

void seen_clear(seen_t *t)
{
    t->count = 0;
    t->added = 0;
    memset(t->head, 0xff, (t->mask + 1) * sizeof(*t->head));
}

static void unlink_slot(seen_t *t, unsigned i)
{
    uint16_t *p = &t->head[hash(t, t->e[i].key)];
    while (*p != i) {
        p = &t->next[*p];
    }
    *p = t->next[i];
}

static unsigned oldest(const seen_t *t, uint32_t now)
{
    unsigned best = 0;
    uint32_t age = 0;
    for (unsigned i = 0; i < t->count; i++) {
        uint32_t a = now - t->e[i].at;
        if (a >= age) {
            age = a;
            best = i;
        }
    }
    return best;
}

static seen_entry_t *find(const seen_t *t, const uint8_t *key, unsigned h)
{
    for (uint16_t i = t->head[h]; i != NIL; i = t->next[i]) {
        if (memcmp(t->e[i].key, key, SEEN_KEY) == 0) {
            return &t->e[i];
        }
    }
    return NULL;
}

static int quiet(const seen_entry_t *e, int8_t rssi, uint32_t now, uint8_t info)
{
    return rssi <= e->hi && (rssi < SEEN_FLOOR || rssi >= e->lo) && now - e->at < SEEN_REFRESH_MS &&
           !(info & ~e->info);
}

seen_result_t seen_check(const seen_t *t, const uint8_t key[SEEN_KEY], int8_t rssi, uint32_t now, uint8_t info)
{
    const seen_entry_t *e = find(t, key, hash(t, key));
    if (!e) {
        return SEEN_NEW;
    }
    return quiet(e, rssi, now, info) ? SEEN_SKIP : SEEN_AGAIN;
}

seen_result_t seen_note(seen_t *t, const uint8_t key[SEEN_KEY], int8_t rssi, uint32_t now, uint8_t info)
{
    unsigned h = hash(t, key);
    seen_entry_t *e = find(t, key, h);
    if (e) {
        if (quiet(e, rssi, now, info)) {
            return SEEN_SKIP;
        }
        if (rssi > e->hi) {
            e->hi = rssi;
        }
        if (rssi >= SEEN_FLOOR && (rssi < e->lo || e->lo < SEEN_FLOOR)) {
            e->lo = rssi;
        }
        e->at = now;
        e->info |= info;
        return SEEN_AGAIN;
    }
    unsigned slot;
    if (t->count < t->cap) {
        slot = t->count++;
    } else {
        slot = oldest(t, now);
        unlink_slot(t, slot);
    }
    e = &t->e[slot];
    memcpy(e->key, key, SEEN_KEY);
    e->hi = e->lo = rssi;
    e->at = now;
    e->info = info;
    e->pad = 0;
    t->next[slot] = t->head[h];
    t->head[h] = slot;
    t->added++;
    return SEEN_NEW;
}
