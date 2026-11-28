#include "wire.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHUNK_MS 600u

static const char *const remote_cmds[] = {"HELLO", "PLAN", "IDENT", "LED", "REBOOT"};

static void put32(uint8_t *p, uint32_t v)
{
    p[0] = v;
    p[1] = v >> 8;
    p[2] = v >> 16;
    p[3] = v >> 24;
}

static uint32_t get32(const uint8_t *p)
{
    return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24;
}

static int head(uint8_t *out, size_t cap, size_t need, uint8_t type)
{
    if (cap < need) {
        return -1;
    }
    out[0] = WIRE_MAGIC;
    out[1] = WIRE_VERSION;
    out[2] = type;
    return 3;
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

static void name_put(uint8_t *out, const char *s)
{
    memset(out, 0, WIRE_NAME);
    for (size_t i = 0; s[i] && i < WIRE_NAME; i++) {
        out[i] = s[i];
    }
}

static void name_get(char *out, const uint8_t *d)
{
    memcpy(out, d, WIRE_NAME);
    out[WIRE_NAME] = 0;
}

void wire_hexid(const uint8_t mac[6], char out[13])
{
    snprintf(out, 13, "%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

int wire_id(const char *s, uint8_t mac[6])
{
    if (!s || strlen(s) != 12) {
        return -1;
    }
    for (int i = 0; i < 6; i++) {
        int hi = hexval(s[2 * i]), lo = hexval(s[2 * i + 1]);
        if (hi < 0 || lo < 0) {
            return -1;
        }
        mac[i] = hi << 4 | lo;
    }
    return 0;
}

int wire_type(const uint8_t *d, size_t n)
{
    if (n < 3 || d[0] != WIRE_MAGIC || d[1] != WIRE_VERSION) {
        return -1;
    }
    return d[2];
}

int wire_join_put(uint8_t *out, size_t cap, const wire_join_t *j)
{
    int n = head(out, cap, 3 + 2 * WIRE_NAME + WIRE_PUB, WIRE_JOIN);
    if (n < 0) {
        return -1;
    }
    name_put(out + n, j->fw);
    name_put(out + n + WIRE_NAME, j->board);
    memcpy(out + n + 2 * WIRE_NAME, j->pub, WIRE_PUB);
    return n + 2 * WIRE_NAME + WIRE_PUB;
}

int wire_join_get(const uint8_t *d, size_t n, wire_join_t *j)
{
    if (wire_type(d, n) != WIRE_JOIN || n != 3 + 2 * WIRE_NAME + WIRE_PUB) {
        return -1;
    }
    name_get(j->fw, d + 3);
    name_get(j->board, d + 3 + WIRE_NAME);
    memcpy(j->pub, d + 3 + 2 * WIRE_NAME, WIRE_PUB);
    return 0;
}

int wire_wait_put(uint8_t *out, size_t cap, const wire_wait_t *w)
{
    int n = head(out, cap, 12, WIRE_WAIT);
    if (n < 0) {
        return -1;
    }
    memcpy(out + 3, w->to, 6);
    out[9] = w->channel;
    out[10] = w->seconds;
    out[11] = w->seconds >> 8;
    return 12;
}

int wire_wait_get(const uint8_t *d, size_t n, wire_wait_t *w)
{
    if (wire_type(d, n) != WIRE_WAIT || n != 12) {
        return -1;
    }
    memcpy(w->to, d + 3, 6);
    w->channel = d[9];
    w->seconds = d[10] | d[11] << 8;
    return 0;
}

int wire_admit_put(uint8_t *out, size_t cap, const wire_admit_t *a)
{
    int n = head(out, cap, 10 + WIRE_PUB + WIRE_KEY + WIRE_CHECK, WIRE_ADMIT);
    if (n < 0) {
        return -1;
    }
    memcpy(out + 3, a->to, 6);
    out[9] = a->channel;
    memcpy(out + 10, a->pub, WIRE_PUB);
    memcpy(out + 10 + WIRE_PUB, a->wrapped, WIRE_KEY);
    memcpy(out + 10 + WIRE_PUB + WIRE_KEY, a->check, WIRE_CHECK);
    return 10 + WIRE_PUB + WIRE_KEY + WIRE_CHECK;
}

int wire_admit_get(const uint8_t *d, size_t n, wire_admit_t *a)
{
    if (wire_type(d, n) != WIRE_ADMIT || n != 10 + WIRE_PUB + WIRE_KEY + WIRE_CHECK) {
        return -1;
    }
    memcpy(a->to, d + 3, 6);
    a->channel = d[9];
    memcpy(a->pub, d + 10, WIRE_PUB);
    memcpy(a->wrapped, d + 10 + WIRE_PUB, WIRE_KEY);
    memcpy(a->check, d + 10 + WIRE_PUB + WIRE_KEY, WIRE_CHECK);
    return 0;
}

int wire_bye_put(uint8_t *out, size_t cap)
{
    return head(out, cap, 3, WIRE_BYE);
}

int wire_data_get(const uint8_t *d, size_t n, wire_data_t *w)
{
    if (wire_type(d, n) != WIRE_DATA || n < WIRE_DATA_HEAD) {
        return -1;
    }
    w->session = get32(d + 3);
    w->seq = get32(d + 7);
    w->now = get32(d + 11);
    w->cmd_ack = d[15];
    w->flags = d[16];
    w->count = d[17];
    w->recs = d + WIRE_DATA_HEAD;
    w->recs_len = n - WIRE_DATA_HEAD;
    size_t pos = 0;
    unsigned count = 0;
    while (pos < w->recs_len) {
        size_t len = w->recs[pos];
        if (len == 0 || pos + WIRE_REC_HEAD + len > w->recs_len) {
            return -1;
        }
        pos += WIRE_REC_HEAD + len;
        count++;
    }
    return count == w->count ? 0 : -1;
}

int wire_rec_next(const wire_data_t *w, size_t *pos, uint32_t *stamp, const char **line, size_t *len)
{
    if (*pos + WIRE_REC_HEAD > w->recs_len) {
        return 0;
    }
    const uint8_t *r = w->recs + *pos;
    *len = r[0];
    *stamp = get32(r + 1);
    *line = (const char *)r + WIRE_REC_HEAD;
    *pos += WIRE_REC_HEAD + *len;
    return 1;
}

int wire_ack_put(uint8_t *out, size_t cap, const wire_peer_t *p, uint32_t session, uint32_t seq, uint8_t flags)
{
    int n = head(out, cap, 13, WIRE_ACK);
    if (n < 0) {
        return -1;
    }
    put32(out + 3, session);
    put32(out + 7, seq);
    out[11] = flags;
    out[12] = 0;
    n = 13;
    for (unsigned i = 0; p && i < p->ncmd; i++) {
        const wire_queued_t *q = &p->cmd[i];
        if ((size_t)n + 2 + q->len > cap) {
            break;
        }
        out[n] = q->id;
        out[n + 1] = q->len;
        memcpy(out + n + 2, q->line, q->len);
        n += 2 + q->len;
        out[12]++;
    }
    return n;
}

static size_t ack_end(const uint8_t *out, size_t n)
{
    size_t pos = 13;
    for (unsigned i = 0; i < out[12] && pos + 2 <= n; i++) {
        pos += 2 + out[pos + 1];
    }
    return pos;
}

int wire_ack_relay(uint8_t *out, size_t cap, size_t n, uint16_t next_ms)
{
    if (n < 13 || ack_end(out, n) != n || n + 3 > cap) {
        return -1;
    }
    out[n] = next_ms;
    out[n + 1] = next_ms >> 8;
    out[n + 2] = 0;
    return n + 3;
}

int wire_ack_carry(uint8_t *out, size_t cap, size_t n, const uint8_t mac[6], uint8_t kind, const wire_queued_t *cmd,
                   unsigned ncmd)
{
    size_t at = ack_end(out, n);
    if (n < 13 || at + 3 > n || out[at + 2] >= WIRE_NODES || ncmd > WIRE_CMDS) {
        return -1;
    }
    size_t need = 8;
    for (unsigned i = 0; i < ncmd; i++) {
        need += 2 + cmd[i].len;
    }
    if (n + need > cap) {
        return -1;
    }
    memcpy(out + n, mac, 6);
    out[n + 6] = kind;
    out[n + 7] = ncmd;
    n += 8;
    for (unsigned i = 0; i < ncmd; i++) {
        out[n] = cmd[i].id;
        out[n + 1] = cmd[i].len;
        memcpy(out + n + 2, cmd[i].line, cmd[i].len);
        n += 2 + cmd[i].len;
    }
    out[at + 2]++;
    return n;
}

static int cmds_get(const uint8_t *d, size_t n, size_t *pos, unsigned count, wire_cmd_t *cmd)
{
    for (unsigned i = 0; i < count; i++) {
        size_t p = *pos;
        if (p + 2 > n || d[p + 1] == 0 || d[p + 1] >= WIRE_CMD_MAX || p + 2 + d[p + 1] > n) {
            return -1;
        }
        cmd[i].id = d[p];
        cmd[i].len = d[p + 1];
        cmd[i].line = (const char *)d + p + 2;
        *pos = p + 2 + d[p + 1];
    }
    return 0;
}

int wire_ack_get(const uint8_t *d, size_t n, wire_ack_t *a)
{
    if (wire_type(d, n) != WIRE_ACK || n < 13 || d[12] > WIRE_CMDS) {
        return -1;
    }
    a->session = get32(d + 3);
    a->seq = get32(d + 7);
    a->flags = d[11];
    a->ncmd = d[12];
    a->next_ms = 0;
    a->ncarried = 0;
    size_t pos = 13;
    if (cmds_get(d, n, &pos, a->ncmd, a->cmd)) {
        return -1;
    }
    if (pos == n) {
        return 0;
    }
    if (pos + 3 > n || d[pos + 2] > WIRE_NODES) {
        return -1;
    }
    a->next_ms = d[pos] | d[pos + 1] << 8;
    a->ncarried = d[pos + 2];
    pos += 3;
    for (unsigned i = 0; i < a->ncarried; i++) {
        wire_carried_t *c = &a->carried[i];
        if (pos + 8 > n || d[pos + 6] > WC_BYE || d[pos + 7] > WIRE_CMDS) {
            return -1;
        }
        memcpy(c->mac, d + pos, 6);
        c->kind = d[pos + 6];
        c->ncmd = d[pos + 7];
        pos += 8;
        if (cmds_get(d, n, &pos, c->ncmd, c->cmd)) {
            return -1;
        }
    }
    return pos == n ? 0 : -1;
}

int wire_offer_put(uint8_t *out, size_t cap, const wire_offer_t *o)
{
    int n = head(out, cap, 13 + WIRE_TAG, WIRE_OFFER);
    if (n < 0) {
        return -1;
    }
    memcpy(out + 3, o->hub, 6);
    out[9] = o->hops;
    out[10] = (uint8_t)o->rssi;
    out[11] = o->next_ms;
    out[12] = o->next_ms >> 8;
    memcpy(out + 13, o->tag, WIRE_TAG);
    return 13 + WIRE_TAG;
}

int wire_offer_get(const uint8_t *d, size_t n, wire_offer_t *o)
{
    if (wire_type(d, n) != WIRE_OFFER || n != 13 + WIRE_TAG) {
        return -1;
    }
    memcpy(o->hub, d + 3, 6);
    o->hops = d[9];
    o->rssi = (int8_t)d[10];
    o->next_ms = d[11] | d[12] << 8;
    memcpy(o->tag, d + 13, WIRE_TAG);
    return o->hops >= 1 && o->hops < WIRE_HOPS ? 0 : -1;
}

int wire_ask_put(uint8_t *out, size_t cap, const uint8_t hub[6])
{
    int n = head(out, cap, 9, WIRE_ASK);
    if (n < 0) {
        return -1;
    }
    memcpy(out + 3, hub, 6);
    return 9;
}

int wire_ask_get(const uint8_t *d, size_t n, uint8_t hub[6])
{
    if (wire_type(d, n) != WIRE_ASK || n != 9) {
        return -1;
    }
    memcpy(hub, d + 3, 6);
    return 0;
}

void wire_offer_signed(const wire_offer_t *o, const uint8_t relay[6], uint8_t *msg, size_t *len)
{
    memcpy(msg, "qm-offer", 8);
    memcpy(msg + 8, relay, 6);
    memcpy(msg + 14, o->hub, 6);
    msg[20] = o->hops;
    *len = 21;
}

void wire_out_init(wire_out_t *o, uint8_t *buf, size_t cap, uint32_t seq)
{
    memset(o, 0, sizeof(*o));
    o->buf = buf;
    o->cap = cap;
    o->seq = seq;
}

static int out_put(wire_out_t *o, uint32_t stamp, const char *line, size_t n, size_t max)
{
    if (n == 0 || n > max || o->len + WIRE_REC_HEAD + n > o->cap) {
        return -1;
    }
    uint8_t *r = o->buf + o->len;
    r[0] = n;
    put32(r + 1, stamp);
    memcpy(r + WIRE_REC_HEAD, line, n);
    o->len += WIRE_REC_HEAD + n;
    return 0;
}

int wire_out_add(wire_out_t *o, uint32_t stamp, const char *line, size_t n)
{
    return out_put(o, stamp, line, n, WIRE_LINE_MAX - 4);
}

int wire_out_carry(wire_out_t *o, uint32_t stamp, const char *line, size_t n)
{
    return out_put(o, stamp, line, n, WIRE_CARRY_MAX);
}

size_t wire_out_free(const wire_out_t *o)
{
    return o->cap - o->len;
}

static void drop_sent(wire_out_t *o)
{
    memmove(o->buf, o->buf + o->sent, o->len - o->sent);
    o->len -= o->sent;
    o->sent = 0;
    o->sent_n = 0;
    o->tries = 0;
    o->seq++;
}

int wire_out_frame(wire_out_t *o, uint8_t *out, size_t cap, const wire_data_t *h)
{
    if (cap < WIRE_DATA_HEAD) {
        return -1;
    }
    if (o->sent && o->tries >= WIRE_TRIES) {
        o->lost += o->sent_n;
        drop_sent(o);
    }
    if (o->sent && WIRE_DATA_HEAD + o->sent > cap) {
        o->sent = 0;
        o->sent_n = 0;
    }
    if (!o->sent) {
        while (o->sent < o->len && o->sent_n < 255) {
            size_t r = WIRE_REC_HEAD + o->buf[o->sent];
            if (WIRE_DATA_HEAD + o->sent + r > cap) {
                break;
            }
            o->sent += r;
            o->sent_n++;
        }
    }
    head(out, cap, WIRE_DATA_HEAD, WIRE_DATA);
    put32(out + 3, h->session);
    put32(out + 7, o->seq);
    put32(out + 11, h->now);
    out[15] = h->cmd_ack;
    out[16] = (h->flags & ~WD_POLL) | (o->sent ? 0 : WD_POLL);
    out[17] = o->sent_n;
    memcpy(out + WIRE_DATA_HEAD, o->buf, o->sent);
    return WIRE_DATA_HEAD + o->sent;
}

int wire_out_poll(const wire_out_t *o, uint8_t *out, size_t cap, const wire_data_t *h)
{
    if (head(out, cap, WIRE_DATA_HEAD, WIRE_DATA) < 0) {
        return -1;
    }
    put32(out + 3, h->session);
    put32(out + 7, o->seq);
    put32(out + 11, h->now);
    out[15] = h->cmd_ack;
    out[16] = h->flags | WD_POLL;
    out[17] = 0;
    return WIRE_DATA_HEAD;
}

int wire_out_ack(wire_out_t *o, uint32_t seq, uint8_t flags)
{
    if (!o->sent || (flags & WA_POLL) || seq != o->seq) {
        return 0;
    }
    drop_sent(o);
    return 1;
}

void wire_out_missed(wire_out_t *o)
{
    if (o->sent && o->tries < WIRE_TRIES) {
        o->tries++;
    }
}

int wire_seen_fresh(wire_seen_t *s, uint8_t id)
{
    if (s->have && (int8_t)(id - s->last) <= 0) {
        return 0;
    }
    s->have = 1;
    s->last = id;
    return 1;
}

void wire_peer_init(wire_peer_t *p, const uint8_t mac[6], uint8_t state, uint32_t now)
{
    memset(p, 0, sizeof(*p));
    memcpy(p->mac, mac, 6);
    p->state = state;
    p->since = now;
    p->next_id = 1;
}

int wire_peer_fresh(const wire_peer_t *p, const wire_data_t *d)
{
    if (d->flags & WD_POLL) {
        return 0;
    }
    return !(p->have_seq && p->session == d->session && (int32_t)(d->seq - p->seq) <= 0);
}

void wire_peer_sync(wire_peer_t *p, int has_ack, uint8_t ack)
{
    if (!p->synced) {
        p->synced = 1;
        if (has_ack) {
            p->next_id = ack + 1;
            for (unsigned i = 0; i < p->ncmd; i++) {
                p->cmd[i].id = p->next_id++;
            }
        }
    } else if (has_ack) {
        unsigned k = 0;
        for (unsigned i = 0; i < p->ncmd; i++) {
            if ((int8_t)(p->cmd[i].id - ack) > 0) {
                p->cmd[k++] = p->cmd[i];
            }
        }
        p->ncmd = k;
    }
}

int wire_peer_data(wire_peer_t *p, const wire_data_t *d, int8_t rssi, uint32_t now)
{
    p->heard = now;
    p->heard_ok = 1;
    p->rssi = rssi;
    if (p->state == PEER_NEW) {
        p->state = PEER_UP;
    }
    wire_peer_sync(p, (d->flags & WD_CMD_ACK) != 0, d->cmd_ack);
    if (d->flags & WD_POLL) {
        return 0;
    }
    if (!wire_peer_fresh(p, d)) {
        p->resent++;
        return 0;
    }
    p->have_seq = 1;
    p->session = d->session;
    p->seq = d->seq;
    p->frames++;
    return 1;
}

void wire_peer_take(wire_peer_t *p, const wire_carried_t *c)
{
    p->synced = 1;
    p->ncmd = 0;
    for (unsigned i = 0; i < c->ncmd && i < WIRE_CMDS; i++) {
        wire_queued_t *q = &p->cmd[p->ncmd++];
        q->id = c->cmd[i].id;
        q->len = c->cmd[i].len;
        memcpy(q->line, c->cmd[i].line, q->len);
        q->line[q->len] = 0;
    }
}

void wire_peer_via(wire_peer_t *p, const uint8_t via[6], const wire_via_t *v, uint32_t now)
{
    memcpy(p->via, via, 6);
    p->has_via = 1;
    p->via_ok = 1;
    p->via_heard = now - (v->ago < WIRE_SILENCE_MS ? v->ago : WIRE_SILENCE_MS);
    p->rssi = v->rssi;
    p->frames = v->frames;
    p->resent = v->resent;
    if (p->state == PEER_NEW) {
        p->state = PEER_UP;
    }
    wire_peer_sync(p, v->has_ack, v->cmd_ack);
}

int wire_peer_up(const wire_peer_t *p, uint32_t now)
{
    return (p->heard_ok && now - p->heard < WIRE_SILENCE_MS) ||
           (p->has_via && p->via_ok && now - p->via_heard < WIRE_SILENCE_MS);
}

int wire_peer_cmd(wire_peer_t *p, const char *line, size_t n)
{
    if (p->ncmd >= WIRE_CMDS || n == 0 || n >= WIRE_CMD_MAX) {
        return -1;
    }
    wire_queued_t *q = &p->cmd[p->ncmd++];
    q->id = p->next_id++;
    q->len = n;
    memcpy(q->line, line, n);
    q->line[n] = 0;
    return 0;
}

void wire_peer_node(const wire_peer_t *p, uint32_t now, wire_node_t *n)
{
    memset(n, 0, sizeof(*n));
    memcpy(n->mac, p->mac, 6);
    n->state = p->state;
    n->heard_ok = p->heard_ok || (p->has_via && p->via_ok);
    n->rssi = p->rssi;
    uint32_t direct = p->heard_ok ? now - p->heard : 0xffffffffu;
    uint32_t relayed = p->has_via && p->via_ok ? now - p->via_heard : 0xffffffffu;
    n->ago = n->heard_ok ? (direct < relayed ? direct : relayed) : 0;
    n->frames = p->frames;
    n->resent = p->resent;
    n->hops = 1;
    if (p->has_via) {
        memcpy(n->via, p->via, 6);
        n->has_via = 1;
        n->hops = 2;
    }
}

void wire_pair_open(wire_pair_t *p, uint32_t now, unsigned seconds)
{
    p->open = seconds > 0;
    p->until = now + seconds * 1000u;
    if (!p->open) {
        memset(p->cand, 0, sizeof(p->cand));
    }
}

unsigned wire_pair_left(wire_pair_t *p, uint32_t now)
{
    if (p->open && (int32_t)(p->until - now) <= 0) {
        p->open = 0;
        memset(p->cand, 0, sizeof(p->cand));
    }
    return p->open ? (p->until - now + 999u) / 1000u : 0;
}

wire_cand_t *wire_pair_find(wire_pair_t *p, const uint8_t mac[6], uint32_t now)
{
    if (!wire_pair_left(p, now)) {
        return NULL;
    }
    for (unsigned i = 0; i < WIRE_NODES; i++) {
        wire_cand_t *c = &p->cand[i];
        if (c->used && !c->clash && memcmp(c->mac, mac, 6) == 0 && now - c->heard < WIRE_JOIN_KEEP_MS) {
            return c;
        }
    }
    return NULL;
}

wire_cand_t *wire_pair_heard(wire_pair_t *p, const uint8_t mac[6], int8_t rssi, const wire_join_t *j, uint32_t now,
                             int *tell)
{
    *tell = 0;
    if (!wire_pair_left(p, now)) {
        return NULL;
    }
    wire_cand_t *c = NULL, *oldest = NULL;
    for (unsigned i = 0; i < WIRE_NODES && !c; i++) {
        wire_cand_t *e = &p->cand[i];
        if (e->used && memcmp(e->mac, mac, 6) == 0) {
            c = e;
        } else if (!e->used) {
            if (!oldest || oldest->used) {
                oldest = e;
            }
        } else if (!e->clash && (!oldest || (oldest->used && (int32_t)(e->heard - oldest->heard) < 0))) {
            oldest = e;
        }
    }
    if (!c) {
        if (!oldest) {
            return NULL;
        }
        c = oldest;
        memset(c, 0, sizeof(*c));
        c->used = 1;
        memcpy(c->mac, mac, 6);
        c->join = *j;
        *tell = 1;
    } else if (c->clash || memcmp(c->join.pub, j->pub, WIRE_PUB) != 0) {
        c->clash = 1;
        return NULL;
    } else if (now - c->told >= WIRE_JOIN_TELL_MS) {
        *tell = 1;
    }
    c->rssi = rssi;
    c->heard = now;
    c->join = *j;
    if (*tell) {
        c->told = now;
    }
    return c;
}

void wire_air_init(wire_air_t *a, int paired, uint8_t home)
{
    memset(a, 0, sizeof(*a));
    a->state = paired ? AIR_SEARCH : AIR_HOP;
    a->home = home >= 1 && home <= WIRE_HOME_MAX ? home : WIRE_HOME_DEFAULT;
    a->ch = a->home;
    a->run = -1;
}

uint8_t wire_air_next(wire_air_t *a, uint32_t now)
{
    switch (a->state) {
    case AIR_WAIT:
        if (now - a->heard < WIRE_WAIT_MS) {
            return a->ch;
        }
        a->state = AIR_HOP;
        __attribute__((fallthrough));
    case AIR_HOP:
        a->ch = a->ch % WIRE_HOME_MAX + 1;
        return a->ch;
    case AIR_SEARCH:
        if (a->tries < WIRE_SEARCH_HOME) {
            a->tries++;
            a->ch = a->home;
            return a->ch;
        }
        a->ch = a->ch % WIRE_HOME_MAX + 1;
        if (++a->tries >= WIRE_SEARCH_HOME + WIRE_HOME_MAX) {
            a->tries = 0;
        }
        return a->ch;
    case AIR_LINKED:
    default:
        a->ch = a->home;
        return a->ch;
    }
}

void wire_air_wait(wire_air_t *a, uint8_t ch, uint32_t now)
{
    if (a->state == AIR_HOP || a->state == AIR_WAIT) {
        a->state = AIR_WAIT;
        a->ch = ch;
        a->heard = now;
    }
}

void wire_air_linked(wire_air_t *a, uint8_t ch, uint32_t now)
{
    a->state = AIR_LINKED;
    a->home = ch;
    a->ch = ch;
    a->tries = 0;
    a->heard = now;
}

int wire_air_lost(wire_air_t *a, uint32_t now)
{
    if (a->state != AIR_LINKED || now - a->heard < WIRE_SILENCE_MS) {
        return 0;
    }
    a->state = AIR_SEARCH;
    a->tries = 0;
    return 1;
}

void wire_air_answer(wire_air_t *a, uint8_t flags)
{
    a->run = (flags & WA_RUN) != 0;
}

int wire_air_run(wire_air_t *a)
{
    int r = a->run;
    a->run = -1;
    return r;
}

int wire_tx_ready(wire_tx_t *t, uint32_t now)
{
    if (t->done != t->issued && now - t->at < WIRE_TX_STALE_MS) {
        return 0;
    }
    t->done = t->issued;
    return 1;
}

void wire_tx_issued(wire_tx_t *t, uint32_t now)
{
    t->issued++;
    t->at = now;
    t->ok = 0;
}

void wire_tx_done(wire_tx_t *t, int ok)
{
    if (t->done != t->issued) {
        t->done++;
        t->ok = ok != 0;
    }
}

int wire_tx_result(const wire_tx_t *t)
{
    return t->done == t->issued ? t->ok : -1;
}

int wire_button(wire_button_t *b, int level, uint32_t now)
{
    if (level) {
        b->armed = 1;
        b->held = 0;
        b->down = 0;
        return 0;
    }
    if (!b->armed || b->held) {
        return 0;
    }
    if (!b->down) {
        b->down = now ? now : 1;
        return 0;
    }
    if (now - b->down < WIRE_HOLD_MS) {
        return 0;
    }
    b->held = 1;
    return 1;
}

static int offer_score(const wire_offer_t *o, int8_t rssi)
{
    int weakest = rssi < o->rssi ? rssi : o->rssi;
    return -(int)o->hops * 1000 + weakest;
}

int wire_offer_better(const wire_offer_t *a, int8_t a_rssi, const wire_offer_t *b, int8_t b_rssi)
{
    return offer_score(a, a_rssi) > offer_score(b, b_rssi);
}

void wire_window_mark(wire_window_t *w, uint32_t now)
{
    if (w->have) {
        uint32_t gap = now - w->last;
        if (gap < 5000u) {
            w->period = w->period ? (w->period * 3 + gap) / 4 : gap;
        }
    }
    w->have = 1;
    w->last = now;
}

uint16_t wire_window_next(const wire_window_t *w, uint32_t now)
{
    if (!w->have || !w->period) {
        return 0;
    }
    uint32_t since = now - w->last;
    uint32_t left = since < w->period ? w->period - since : 0;
    return left > 3000u ? 3000u : left;
}

uint32_t wire_relay_budget(uint32_t now, int known, uint32_t next_at, uint32_t last_answer)
{
    int32_t until = (int32_t)(next_at - now);
    if (known && until > (int32_t)WIRE_EARLY_MS && now - last_answer < 4000u) {
        return 0;
    }
    if (!known || until < 0) {
        return WIRE_EARLY_MS + WIRE_LATE_MS;
    }
    return (uint32_t)until + WIRE_LATE_MS;
}

void wire_chunk(uint16_t *c2, uint32_t *c5, unsigned n, uint16_t *o2, uint32_t *o5)
{
    *o2 = 0;
    *o5 = 0;
    for (unsigned b = 1; b < 16 && n; b++) {
        if (*c2 & (1u << b)) {
            *c2 &= ~(1u << b);
            *o2 |= 1u << b;
            n--;
        }
    }
    for (unsigned b = 1; b < 32 && n; b++) {
        if (*c5 & (1u << b)) {
            *c5 &= ~(1u << b);
            *o5 |= 1u << b;
            n--;
        }
    }
}

unsigned wire_chunk_size(unsigned dwell)
{
    unsigned n = CHUNK_MS / (dwell + 20u);
    return n ? n : 1;
}

const char *wire_hub_args(const proto_cmd_t *c, uint8_t *home, uint8_t key[WIRE_KEY])
{
    if (c->argc != 2) {
        return "args";
    }
    const char *ch = c->argv[0];
    if (!*ch) {
        *home = WIRE_HOME_DEFAULT;
    } else {
        if (strlen(ch) > 2 || ch[0] < '0' || ch[0] > '9' || (ch[1] && (ch[1] < '0' || ch[1] > '9'))) {
            return "channel";
        }
        int v = atoi(ch);
        if (v < 1 || v > WIRE_HOME_MAX) {
            return "channel";
        }
        *home = v;
    }
    const char *k = c->argv[1];
    if (strlen(k) != 2 * WIRE_KEY) {
        return "key";
    }
    uint8_t any = 0;
    for (int i = 0; i < WIRE_KEY; i++) {
        int hi = hexval(k[2 * i]), lo = hexval(k[2 * i + 1]);
        if (hi < 0 || lo < 0) {
            return "key";
        }
        key[i] = hi << 4 | lo;
        any |= key[i];
    }
    return any ? NULL : "key";
}

const char *wire_pair_args(const proto_cmd_t *c, unsigned *seconds)
{
    if (c->argc > 1) {
        return "seconds";
    }
    if (c->argc == 0 || !*c->argv[0]) {
        *seconds = WIRE_PAIR_DEFAULT;
        return NULL;
    }
    const char *s = c->argv[0];
    if (strlen(s) > 3) {
        return "seconds";
    }
    for (const char *q = s; *q; q++) {
        if (*q < '0' || *q > '9') {
            return "seconds";
        }
    }
    unsigned v = atoi(s);
    if (v > WIRE_PAIR_MAX) {
        return "seconds";
    }
    *seconds = v;
    return NULL;
}

int wire_remote_ok(const char *name)
{
    for (size_t i = 0; i < sizeof(remote_cmds) / sizeof(remote_cmds[0]); i++) {
        if (strcmp(name, remote_cmds[i]) == 0) {
            return 1;
        }
    }
    return 0;
}

const char *wire_to_args(const proto_cmd_t *c, uint8_t mac[6], char *line, size_t cap)
{
    if (c->argc < 2 || c->argc > PROTO_ARGS_MAX) {
        return "args";
    }
    if (wire_id(c->argv[0], mac)) {
        return "node";
    }
    if (!wire_remote_ok(c->argv[1])) {
        return "command";
    }
    int n = proto_put(line, cap, 0, "#%s", c->argv[1]);
    for (int i = 2; i < c->argc; i++) {
        n = proto_put(line, cap, n, ",%s", c->argv[i]);
    }
    if (n < 0 || n >= WIRE_CMD_MAX) {
        return "args";
    }
    return NULL;
}

static int number_end(const char *s, size_t n, size_t pos, size_t *end, unsigned long long *v)
{
    *v = 0;
    size_t i = pos;
    while (i < n && s[i] >= '0' && s[i] <= '9' && i - pos < 10) {
        *v = *v * 10 + (s[i] - '0');
        i++;
    }
    *end = i;
    return i > pos && (i == n || s[i] == ',');
}

int wire_inner(const char *line, size_t n, uint8_t id[6], const char **inner, size_t *len)
{
    char hex[13];
    if (n < 18 || strncmp(line, "@R,", 3) || line[15] != ',' || line[16] != '@') {
        return 0;
    }
    memcpy(hex, line + 3, 12);
    hex[12] = 0;
    for (int i = 0; i < 12; i++) {
        if (hexval(hex[i]) < 0 || (hex[i] >= 'A' && hex[i] <= 'F')) {
            return 0;
        }
    }
    if (wire_id(hex, id)) {
        return 0;
    }
    *inner = line + 16;
    *len = n - 16;
    return 1;
}

int wire_carry(char *out, size_t cap, const uint8_t mac[6], const char *line, size_t n, uint32_t extra, int sum,
               uint8_t id[6])
{
    uint8_t who[6];
    char hex[13];
    const char *body = line;
    size_t blen = n;
    if (!wire_inner(line, n, who, &body, &blen)) {
        memcpy(who, mac, 6);
    }
    if (blen < 3 || body[0] != '@' || body[2] != ',' || body[1] == 'R' || memchr(body, '*', blen) ||
        memchr(body, '\n', blen)) {
        return -1;
    }
    if (id) {
        memcpy(id, who, 6);
    }
    wire_hexid(who, hex);
    int len = proto_put(out, cap, 0, "@R,%s,", hex);
    if (len < 0) {
        return -1;
    }
    const char *comma = (body[1] == 'W' || body[1] == 'B') ? memchr(body + 3, ',', blen - 3) : NULL;
    size_t end;
    unsigned long long age;
    if (comma && number_end(body, blen, comma - body + 1, &end, &age)) {
        size_t start = comma - body + 1;
        age += extra;
        if (age > 0xffffffffull) {
            age = 0xffffffffull;
        }
        if ((size_t)len + start >= cap) {
            return -1;
        }
        memcpy(out + len, body, start);
        len = proto_put(out, cap, len + start, "%lu", (unsigned long)age);
        if (len < 0 || (size_t)len + (blen - end) >= cap) {
            return -1;
        }
        memcpy(out + len, body + end, blen - end);
        len += blen - end;
    } else {
        if ((size_t)len + blen >= cap) {
            return -1;
        }
        memcpy(out + len, body, blen);
        len += blen;
    }
    out[len] = 0;
    return sum ? proto_end(out, cap, len) : len;
}

int wire_relay(char *out, size_t cap, const uint8_t mac[6], const char *line, size_t n, uint32_t extra)
{
    return wire_carry(out, cap, mac, line, n, extra, 1, NULL);
}

int wire_via_line(char *out, size_t cap, const wire_via_t *v)
{
    char id[13];
    wire_hexid(v->id, id);
    int n = proto_put(out, cap, 0, "@V,%s,%lu,%d,%lu,%lu,", id, (unsigned long)v->ago, v->rssi,
                      (unsigned long)v->frames, (unsigned long)v->resent);
    if (v->has_ack) {
        n = proto_put(out, cap, n, "%u", v->cmd_ack);
    }
    return n;
}

static int field_number(const char **s, const char *end, long long lo, long long hi, long long *v)
{
    const char *p = *s;
    int neg = 0;
    if (p < end && *p == '-') {
        neg = 1;
        p++;
    }
    const char *digits = p;
    long long x = 0;
    while (p < end && *p >= '0' && *p <= '9' && p - digits < 10) {
        x = x * 10 + (*p - '0');
        p++;
    }
    if (p == digits || (p < end && *p != ',')) {
        return -1;
    }
    x = neg ? -x : x;
    if (x < lo || x > hi) {
        return -1;
    }
    *v = x;
    *s = p < end ? p + 1 : p;
    return 0;
}

int wire_via_get(const char *line, size_t n, wire_via_t *v)
{
    const char *end = line + n;
    char hex[13];
    long long ago, rssi, frames, resent, ack;
    if (n < 19 || strncmp(line, "@V,", 3) || line[15] != ',') {
        return -1;
    }
    memcpy(hex, line + 3, 12);
    hex[12] = 0;
    const char *p = line + 16;
    if (wire_id(hex, v->id) || field_number(&p, end, 0, 0xffffffffll, &ago) ||
        field_number(&p, end, -128, 127, &rssi) || field_number(&p, end, 0, 0xffffffffll, &frames) ||
        field_number(&p, end, 0, 0xffffffffll, &resent) || p[-1] != ',') {
        return -1;
    }
    v->ago = ago;
    v->rssi = rssi;
    v->frames = frames;
    v->resent = resent;
    v->has_ack = 0;
    v->cmd_ack = 0;
    if (p < end) {
        if (field_number(&p, end, 0, 255, &ack) || p != end) {
            return -1;
        }
        v->has_ack = 1;
        v->cmd_ack = ack;
    }
    return 0;
}

size_t wire_relay_need(const uint8_t mac[6], const wire_data_t *d)
{
    char out[PROTO_LINE_MAX + 1];
    size_t pos = 0, len, need = 0;
    uint32_t stamp;
    const char *l;
    while (wire_rec_next(d, &pos, &stamp, &l, &len)) {
        int n = wire_relay(out, sizeof(out), mac, l, len, d->now - stamp);
        if (n > 0) {
            need += WIRE_RING_ITEM(n);
        }
    }
    return need;
}

int wire_unrelay(const char *in, size_t n, char id[13], char *out, size_t cap)
{
    while (n && (in[n - 1] == '\n' || in[n - 1] == '\r')) {
        n--;
    }
    if (n < 21 || strncmp(in, "@R,", 3) || in[15] != ',' || in[16] != '@' || in[n - 3] != '*') {
        return -1;
    }
    int hi = hexval(in[n - 2]), lo = hexval(in[n - 1]);
    if (hi < 0 || lo < 0 || proto_xor(in + 1, n - 4) != (uint8_t)(hi << 4 | lo)) {
        return -1;
    }
    memcpy(id, in + 3, 12);
    id[12] = 0;
    size_t body = n - 3 - 16;
    if (body + 5 > cap) {
        return -1;
    }
    memcpy(out, in + 16, body);
    out[body] = 0;
    return proto_end(out, cap, body);
}

int wire_cluster_line(char *out, size_t cap, uint32_t seq, const wire_cluster_t *c)
{
    int n = proto_put(out, cap, 0, "@C,%lu,%u,%u,%u,%s,%lu,%lu", (unsigned long)seq, c->home, c->left, c->nodes,
                      c->running ? "run" : "idle", (unsigned long)c->relayed, (unsigned long)c->dropped);
    return proto_end(out, cap, n);
}

static const char *state_name(const wire_node_t *n)
{
    if (n->state == PEER_NEW) {
        return "new";
    }
    return n->heard_ok && n->ago < WIRE_SILENCE_MS ? "up" : "lost";
}

int wire_node_line(char *out, size_t cap, uint32_t seq, const wire_node_t *n)
{
    char id[13];
    wire_hexid(n->mac, id);
    int len = proto_put(out, cap, 0, "@N,%lu,%s,%s,", (unsigned long)seq, id, state_name(n));
    if (n->heard_ok) {
        len = proto_put(out, cap, len, "%lu,%d", (unsigned long)n->ago, n->rssi);
    } else {
        len = proto_put(out, cap, len, ",");
    }
    len = proto_put(out, cap, len, ",%lu,%lu,", (unsigned long)n->frames, (unsigned long)n->resent);
    if (n->has_via) {
        char via[13];
        wire_hexid(n->via, via);
        len = proto_put(out, cap, len, "%s", via);
    }
    len = proto_put(out, cap, len, ",%u", n->hops);
    return proto_end(out, cap, len);
}

int wire_join_line(char *out, size_t cap, uint32_t seq, const wire_joiner_t *j)
{
    char id[13];
    wire_hexid(j->mac, id);
    int n = proto_put(out, cap, 0, "@J,%lu,%s,%d,", (unsigned long)seq, id, j->rssi);
    n = proto_text(out, cap, n, j->fw, WIRE_NAME);
    n = proto_put(out, cap, n, ",");
    n = proto_text(out, cap, n, j->board, WIRE_NAME);
    return proto_end(out, cap, n);
}
