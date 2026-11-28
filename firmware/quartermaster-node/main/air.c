#include <string.h>

#include "esp_heap_caps.h"
#include "esp_random.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "node.h"

#define ACK_WAIT_MS 40
#define HANDOVER_MS 150
#define POLL_IDLE_MS 2000
#define HOP_LISTEN_MS 150
#define WAIT_LISTEN_MS 1000
#define SEARCH_PAUSE_MS 150
#define REBOOT_WAIT_MS 3000
#define RETRY_SEND_MS 10
#define LOOK_MS 300
#define SEARCH_LOOK_MS 1000
#define IDLE_LISTEN_MS 100
#define FWD_MAX 8

typedef struct {
    uint8_t hub[6];
    uint8_t key[WIRE_KEY];
    uint8_t home;
} pairing_t;

typedef struct {
    wire_peer_t p;
    uint8_t used, bye, has_ack, cmd_ack, told;
    uint32_t reported;
} kid_t;

typedef struct {
    uint8_t used, kid;
    uint8_t mac[6];
    uint32_t at;
} route_t;

typedef struct {
    uint8_t used, kind, ncmd;
    uint8_t mac[6];
    uint32_t at;
    wire_queued_t cmd[WIRE_CMDS];
} fwd_t;

static SemaphoreHandle_t out_lock, radio_lock;
static TaskHandle_t task;
static wire_out_t out;
static size_t out_cap;
static wire_air_t st;
static pairing_t pair;
static bool paired, have_kp;
static uint8_t self[6], pub[WIRE_PUB];
static uint32_t session, last_poll;
static wire_seen_t cmds;
static uint8_t frame[WIRE_FRAME_MAX], reply[WIRE_FRAME_MAX];
static char cmd[WIRE_CMD_MAX + 1];
static char carried[PROTO_LINE_MAX + 1];
static volatile bool reboot_pending;
static volatile uint32_t reboot_at;
static uint8_t answer;
static wire_ack_t ack;

static uint8_t parent[6], hops = 1;
static bool via_relay, next_known, parent_relays;
static uint32_t next_at, last_answer, last_hub_try;
static int8_t hub_run = -1, parent_rssi;
static kid_t kids[WIRE_KIDS];
static route_t routes[WIRE_ROUTES];
static fwd_t fwds[FWD_MAX];
static wire_window_t windows;
static unsigned handovers;

static void link_hub(void)
{
    radio_pmk(pair.key);
    radio_peer(pair.hub, pair.key, self);
}

static void to_hub(void)
{
    if (via_relay) {
        radio_unpeer(parent);
    }
    memcpy(parent, pair.hub, 6);
    via_relay = false;
    hops = 1;
    next_known = false;
}

static void drop_kid(kid_t *k)
{
    radio_unpeer(k->p.mac);
    for (unsigned i = 0; i < WIRE_ROUTES; i++) {
        if (routes[i].used && routes[i].kid == k - kids) {
            routes[i].used = 0;
        }
    }
    memset(k, 0, sizeof(*k));
}

static void drop_kids(void)
{
    for (unsigned i = 0; i < WIRE_KIDS; i++) {
        if (kids[i].used) {
            drop_kid(&kids[i]);
        }
    }
    memset(routes, 0, sizeof(routes));
    memset(fwds, 0, sizeof(fwds));
}

static void unpair(void)
{
    drop_kids();
    if (paired) {
        to_hub();
        radio_unpeer(pair.hub);
    }
    paired = false;
    memset(&pair, 0, sizeof(pair));
    memset(&cmds, 0, sizeof(cmds));
    node_erase("air");
    wire_air_init(&st, 0, 0);
    if (node.role == ROLE_AIR) {
        node_run(false);
    }
}

static bool empty(void)
{
    xSemaphoreTake(out_lock, portMAX_DELAY);
    bool e = out.len == 0;
    xSemaphoreGive(out_lock);
    return e;
}

static kid_t *kid_of(const uint8_t mac[6])
{
    for (unsigned i = 0; i < WIRE_KIDS; i++) {
        if (kids[i].used && memcmp(kids[i].p.mac, mac, 6) == 0) {
            return &kids[i];
        }
    }
    return NULL;
}

static route_t *route_of(const uint8_t mac[6], uint32_t now)
{
    for (unsigned i = 0; i < WIRE_ROUTES; i++) {
        route_t *r = &routes[i];
        if (r->used && memcmp(r->mac, mac, 6) == 0) {
            if (now - r->at < WIRE_KID_KEEP_MS) {
                return r;
            }
            r->used = 0;
        }
    }
    return NULL;
}

static bool below(const uint8_t mac[6], uint32_t now)
{
    return kid_of(mac) || route_of(mac, now);
}

static void learn(const uint8_t who[6], const kid_t *k, uint32_t now)
{
    if (memcmp(who, k->p.mac, 6) == 0) {
        return;
    }
    route_t *r = route_of(who, now), *free = NULL;
    for (unsigned i = 0; i < WIRE_ROUTES && !r; i++) {
        if (!routes[i].used) {
            free = &routes[i];
            break;
        }
        if (!free || (int32_t)(routes[i].at - free->at) < 0) {
            free = &routes[i];
        }
    }
    if (!r) {
        r = free;
        memcpy(r->mac, who, 6);
        r->used = 1;
    }
    r->kid = k - kids;
    r->at = now;
}

static void store_fwd(const wire_carried_t *c, uint32_t now)
{
    fwd_t *f = NULL, *old = &fwds[0];
    for (unsigned i = 0; i < FWD_MAX && !f; i++) {
        if (fwds[i].used && memcmp(fwds[i].mac, c->mac, 6) == 0) {
            f = &fwds[i];
        } else if (!fwds[i].used) {
            old = &fwds[i];
        } else if (old->used && (int32_t)(fwds[i].at - old->at) < 0) {
            old = &fwds[i];
        }
    }
    if (!f) {
        f = old;
    }
    memcpy(f->mac, c->mac, 6);
    f->used = 1;
    f->kind = c->kind;
    f->ncmd = c->ncmd;
    f->at = now;
    for (unsigned i = 0; i < c->ncmd && i < WIRE_CMDS; i++) {
        f->cmd[i].id = c->cmd[i].id;
        f->cmd[i].len = c->cmd[i].len;
        memcpy(f->cmd[i].line, c->cmd[i].line, c->cmd[i].len);
        f->cmd[i].line[c->cmd[i].len] = 0;
    }
}

static void carried_in(const wire_ack_t *a, uint32_t now)
{
    for (unsigned i = 0; i < a->ncarried; i++) {
        const wire_carried_t *c = &a->carried[i];
        kid_t *k = kid_of(c->mac);
        if (k) {
            if (c->kind == WC_BYE) {
                k->bye = 1;
            } else {
                wire_peer_take(&k->p, c);
            }
        } else if (route_of(c->mac, now)) {
            store_fwd(c, now);
        }
    }
}

static void acked(const wire_ack_t *a)
{
    uint32_t now = node_ms();
    xSemaphoreTake(out_lock, portMAX_DELAY);
    wire_out_ack(&out, a->seq, a->flags);
    xSemaphoreGive(out_lock);
    answer = a->flags;
    last_answer = now;
    if (a->flags & WA_BYE) {
        unpair();
        uplink_log("wireless: hub let this node go");
        return;
    }
    hub_run = (a->flags & WA_RUN) != 0;
    parent_relays = (a->flags & WA_RELAY) != 0;
    if (via_relay) {
        next_known = a->next_ms != 0;
        next_at = now + a->next_ms;
    }
    carried_in(a, now);
    wire_air_answer(&st, a->flags);
    for (unsigned i = 0; i < a->ncmd; i++) {
        if (wire_seen_fresh(&cmds, a->cmd[i].id)) {
            memcpy(cmd, a->cmd[i].line, a->cmd[i].len);
            cmd[a->cmd[i].len] = 0;
            node_remote(cmd, a->cmd[i].len);
        }
    }
}

static bool can_relay(uint32_t now)
{
    return paired && parent_relays && st.state == AIR_LINKED && hops < WIRE_HOPS &&
           now - last_answer < WIRE_PARENT_QUIET_MS;
}

static void offer(void)
{
    wire_offer_t o = {.hops = hops, .rssi = parent_rssi, .next_ms = node.running ? wire_window_next(&windows, node_ms()) : 0};
    uint8_t msg[32];
    size_t n = 0;
    memcpy(o.hub, pair.hub, 6);
    wire_offer_signed(&o, self, msg, &n);
    radio_tag(pair.key, msg, n, o.tag);
    int len = wire_offer_put(reply, sizeof(reply), &o);
    radio_send(NULL, reply, len);
}

static void asked(const radio_rx_t *r, uint32_t now)
{
    uint8_t hub[6];
    if (wire_ask_get(r->data, r->len, hub) || memcmp(hub, pair.hub, 6) || !can_relay(now) ||
        memcmp(r->mac, parent, 6) == 0 || route_of(r->mac, now)) {
        return;
    }
    kid_t *k = kid_of(r->mac);
    for (unsigned i = 0; i < WIRE_KIDS && !k; i++) {
        if (!kids[i].used) {
            k = &kids[i];
            if (!radio_peer(r->mac, pair.key, r->mac)) {
                return;
            }
            wire_peer_init(&k->p, r->mac, PEER_NEW, now);
            k->p.synced = 1;
            k->used = 1;
            k->p.heard = now;
        }
    }
    if (k) {
        offer();
    }
}

static void tell_via(kid_t *k, uint32_t now)
{
    wire_via_t v = {
        .ago = k->p.heard_ok ? now - k->p.heard : WIRE_SILENCE_MS,
        .rssi = k->p.rssi,
        .frames = k->p.frames,
        .resent = k->p.resent,
        .has_ack = k->has_ack,
        .cmd_ack = k->cmd_ack,
    };
    memcpy(v.id, k->p.mac, 6);
    int n = wire_via_line(carried, sizeof(carried), &v);
    if (n > 0) {
        xSemaphoreTake(out_lock, portMAX_DELAY);
        wire_out_add(&out, now, carried, n);
        xSemaphoreGive(out_lock);
    }
    k->reported = now;
    k->told = 1;
}

static size_t need_of(const wire_data_t *d)
{
    size_t pos = 0, len, need = 0;
    uint32_t stamp;
    const char *l;
    while (wire_rec_next(d, &pos, &stamp, &l, &len)) {
        need += WIRE_REC_HEAD + len + 16 + 10;
    }
    return need;
}

static void take_lines(kid_t *k, const wire_data_t *d, uint32_t now)
{
    size_t pos = 0, len;
    uint32_t stamp;
    const char *l;
    uint8_t who[6];
    while (wire_rec_next(d, &pos, &stamp, &l, &len)) {
        int n = wire_carry(carried, sizeof(carried), k->p.mac, l, len, d->now - stamp, 0, who);
        if (n <= 0) {
            continue;
        }
        learn(who, k, now);
        xSemaphoreTake(out_lock, portMAX_DELAY);
        int err = wire_out_carry(&out, now, carried, n);
        xSemaphoreGive(out_lock);
        if (err) {
            __atomic_add_fetch(&node.dropped, 1, __ATOMIC_RELAXED);
        }
    }
}

static void kid_data(const radio_rx_t *r, uint32_t now)
{
    kid_t *k = kid_of(r->mac);
    wire_data_t d;
    if (!k || wire_data_get(r->data, r->len, &d)) {
        return;
    }
    uint8_t flags = (hub_run > 0 ? WA_RUN : 0) | (d.flags & WD_POLL ? WA_POLL : 0) | WA_RELAY;
    if (k->bye) {
        int n = wire_ack_put(reply, sizeof(reply), NULL, d.session, d.seq, flags | WA_BYE);
        radio_send(k->p.mac, reply, n);
        drop_kid(k);
        return;
    }
    if (wire_peer_fresh(&k->p, &d)) {
        xSemaphoreTake(out_lock, portMAX_DELAY);
        bool room = need_of(&d) <= wire_out_free(&out);
        xSemaphoreGive(out_lock);
        if (!room) {
            d.flags |= WD_POLL;
            flags |= WA_POLL | WA_BUSY;
        }
    }
    if (d.flags & WD_CMD_ACK) {
        k->has_ack = 1;
        k->cmd_ack = d.cmd_ack;
    }
    int fresh = wire_peer_data(&k->p, &d, r->rssi, now);
    int n = wire_ack_put(reply, radio_max(), &k->p, d.session, d.seq, flags);
    n = wire_ack_relay(reply, radio_max(), n, node.running ? wire_window_next(&windows, now) : 0);
    for (unsigned i = 0; i < FWD_MAX && n > 0; i++) {
        fwd_t *f = &fwds[i];
        if (!f->used) {
            continue;
        }
        route_t *rt = route_of(f->mac, now);
        if (now - f->at >= WIRE_FWD_KEEP_MS || !rt) {
            f->used = 0;
        } else if (&kids[rt->kid] == k) {
            int m = wire_ack_carry(reply, radio_max(), n, f->mac, f->kind, f->cmd, f->ncmd);
            if (m < 0) {
                break;
            }
            n = m;
        }
    }
    if (n > 0) {
        radio_send(k->p.mac, reply, n);
    }
    if (fresh) {
        take_lines(k, &d, now);
    }
    if (!k->told) {
        tell_via(k, now);
    }
}

static void serve(const radio_rx_t *r, uint32_t now)
{
    int type = wire_type(r->data, r->len);
    if (type == WIRE_ASK) {
        asked(r, now);
    } else if (type == WIRE_DATA) {
        kid_data(r, now);
    }
}

static void tend(uint32_t now)
{
    for (unsigned i = 0; i < WIRE_KIDS; i++) {
        kid_t *k = &kids[i];
        if (!k->used) {
            continue;
        }
        if (now - k->p.heard >= WIRE_KID_KEEP_MS) {
            drop_kid(k);
        } else if (k->p.heard_ok && now - k->reported >= WIRE_VIA_MS) {
            tell_via(k, now);
        }
    }
}

static unsigned kid_count(void)
{
    unsigned n = 0;
    for (unsigned i = 0; i < WIRE_KIDS; i++) {
        n += kids[i].used;
    }
    return n;
}

static void listen(uint32_t ms)
{
    uint32_t start = node_ms();
    for (;;) {
        uint32_t spent = node_ms() - start;
        radio_rx_t *r = spent < ms ? radio_recv(ms - spent) : NULL;
        if (!r) {
            return;
        }
        serve(r, node_ms());
        radio_done(r);
    }
}

static void window(void)
{
    uint32_t now = node_ms();
    if (!can_relay(now)) {
        return;
    }
    bool busy = kid_count() > 0;
    if (!busy && handovers % WIRE_WINDOW_EVERY) {
        return;
    }
    wire_window_mark(&windows, now);
    offer();
    listen(busy ? WIRE_WINDOW_MS : WIRE_WINDOW_LONE_MS);
    tend(node_ms());
}

static bool wait_ack(const uint8_t to[6], uint32_t ms)
{
    uint32_t start = node_ms();
    for (;;) {
        uint32_t spent = node_ms() - start;
        radio_rx_t *r = spent < ms ? radio_recv(ms - spent) : NULL;
        if (!r) {
            return false;
        }
        bool done = false;
        if (paired && memcmp(r->mac, to, 6) == 0) {
            int type = wire_type(r->data, r->len);
            if (type == WIRE_ACK && wire_ack_get(r->data, r->len, &ack) == 0 && ack.session == session) {
                parent_rssi = r->rssi;
                acked(&ack);
                done = true;
            } else if (type == WIRE_BYE && memcmp(to, pair.hub, 6) == 0) {
                unpair();
                uplink_log("wireless: hub let this node go");
            }
        } else if (paired) {
            serve(r, node_ms());
        }
        radio_done(r);
        if (done || !paired) {
            return done;
        }
    }
}

static bool exchange_with(const uint8_t to[6], uint8_t ch, uint32_t budget, bool search, bool patient)
{
    if (!out.buf || !radio_channel(ch)) {
        return false;
    }
    uint32_t start = node_ms();
    bool heard = false;
    unsigned misses = 0;
    while (paired && node.role == ROLE_AIR) {
        wire_data_t h = {
            .session = session,
            .now = node_ms(),
            .cmd_ack = cmds.last,
            .flags = (cmds.have ? WD_CMD_ACK : 0) | (node.running ? WD_RUN : 0) | WD_RELAY,
        };
        xSemaphoreTake(out_lock, portMAX_DELAY);
        uint32_t lost = out.lost;
        int n = search && !heard ? wire_out_poll(&out, frame, sizeof(frame), &h)
                                 : wire_out_frame(&out, frame, radio_max(), &h);
        lost = out.lost - lost;
        xSemaphoreGive(out_lock);
        if (lost) {
            __atomic_add_fetch(&node.dropped, lost, __ATOMIC_RELAXED);
        }
        bool poll = n >= WIRE_DATA_HEAD && (frame[16] & WD_POLL);
        bool delivered = n > 0 && radio_send(to, frame, n);
        if (delivered && wait_ack(to, ACK_WAIT_MS)) {
            heard = true;
            misses = 0;
            wire_air_linked(&st, ch, node_ms());
            if ((poll && !search) || (answer & WA_BUSY) || empty()) {
                break;
            }
        } else if (!delivered && patient && !heard) {
            vTaskDelay(pdMS_TO_TICKS(RETRY_SEND_MS));
        } else {
            if (delivered && paired) {
                xSemaphoreTake(out_lock, portMAX_DELAY);
                wire_out_missed(&out);
                xSemaphoreGive(out_lock);
            }
            if (++misses >= 2) {
                break;
            }
        }
        if (node_ms() - start >= budget) {
            break;
        }
    }
    int run = wire_air_run(&st);
    if (run >= 0 && paired && node.role == ROLE_AIR) {
        node_run(run);
    }
    return heard;
}

static bool exchange(uint8_t ch, uint32_t budget, bool search)
{
    return exchange_with(parent, ch, budget, search, false);
}

static bool look(uint32_t ms)
{
    uint32_t now = node_ms();
    if (!radio_channel(st.home)) {
        return false;
    }
    int n = wire_ask_put(frame, sizeof(frame), pair.hub);
    radio_send(NULL, frame, n);
    wire_offer_t best;
    int8_t best_rssi = 0;
    uint8_t best_mac[6];
    bool found = false;
    uint32_t start = node_ms();
    for (;;) {
        uint32_t spent = node_ms() - start;
        radio_rx_t *r = spent < ms ? radio_recv(ms - spent) : NULL;
        if (!r) {
            break;
        }
        wire_offer_t o;
        if (wire_type(r->data, r->len) == WIRE_OFFER && wire_offer_get(r->data, r->len, &o) == 0 &&
            memcmp(o.hub, pair.hub, 6) == 0 && !below(r->mac, now) && memcmp(r->mac, self, 6)) {
            uint8_t msg[32], tag[WIRE_TAG];
            size_t len = 0;
            wire_offer_signed(&o, r->mac, msg, &len);
            radio_tag(pair.key, msg, len, tag);
            if (memcmp(tag, o.tag, WIRE_TAG) == 0 && (!found || wire_offer_better(&o, r->rssi, &best, best_rssi))) {
                best = o;
                best_rssi = r->rssi;
                memcpy(best_mac, r->mac, 6);
                found = true;
            }
        } else {
            serve(r, node_ms());
        }
        radio_done(r);
    }
    if (!found || !radio_peer(best_mac, pair.key, self)) {
        return false;
    }
    if (via_relay && memcmp(parent, best_mac, 6)) {
        radio_unpeer(parent);
    }
    memcpy(parent, best_mac, 6);
    via_relay = true;
    hops = best.hops + 1;
    next_known = best.next_ms != 0;
    next_at = node_ms() + best.next_ms;
    last_answer = node_ms();
    uplink_log("wireless: through a node, %u hops", hops);
    return true;
}

static bool try_hub(uint8_t ch)
{
    last_hub_try = node_ms();
    if (exchange_with(pair.hub, ch, HANDOVER_MS, true, false)) {
        to_hub();
        last_answer = node_ms();
        return true;
    }
    return false;
}

static bool handover(bool idle)
{
    uint32_t now = node_ms();
    bool heard;
    if (via_relay) {
        if (now - last_hub_try >= WIRE_HUB_RETRY_MS && try_hub(st.home)) {
            return true;
        }
        uint32_t budget = idle ? HANDOVER_MS : wire_relay_budget(now, next_known, next_at, last_answer);
        if (!budget) {
            return false;
        }
        heard = exchange_with(parent, st.home, budget, false, true);
    } else {
        heard = exchange(st.home, HANDOVER_MS, false);
    }
    if (!heard && paired && node_ms() - last_answer >= WIRE_PARENT_QUIET_MS) {
        if (!(via_relay && try_hub(st.home)) && look(LOOK_MS)) {
            heard = exchange_with(parent, st.home, HANDOVER_MS, false, true);
        }
    }
    return heard;
}

static void admitted(const radio_rx_t *r, const wire_admit_t *a)
{
    uint8_t kek[32], key[WIRE_KEY];
    if (!have_kp || !radio_kek(a->pub, self, r->mac, kek)) {
        return;
    }
    bool ok = radio_unwrap(kek, a->wrapped, a->check, key);
    memset(kek, 0, sizeof(kek));
    if (!ok) {
        return;
    }
    radio_kp_free();
    have_kp = false;
    memcpy(pair.hub, r->mac, 6);
    memcpy(pair.key, key, WIRE_KEY);
    pair.home = a->channel >= 1 && a->channel <= WIRE_HOME_MAX ? a->channel : WIRE_HOME_DEFAULT;
    node_save("air", &pair, sizeof(pair));
    paired = true;
    memset(&cmds, 0, sizeof(cmds));
    link_hub();
    to_hub();
    last_answer = node_ms();
    wire_air_init(&st, 1, pair.home);
    uplink_hello();
}

static uint32_t join_step(uint32_t now)
{
    if (!have_kp) {
        have_kp = radio_kp_new(pub);
        if (!have_kp) {
            return 1000;
        }
    }
    uint8_t ch = wire_air_next(&st, now);
    if (!radio_channel(ch)) {
        return 100;
    }
    proto_hello_t h;
    node_hello(&h);
    wire_join_t j;
    memset(&j, 0, sizeof(j));
    strncpy(j.fw, NODE_FW, WIRE_NAME);
    strncpy(j.board, h.board, WIRE_NAME);
    memcpy(j.pub, pub, WIRE_PUB);
    int n = wire_join_put(frame, sizeof(frame), &j);
    radio_send(NULL, frame, n);
    uint32_t listen_ms = st.state == AIR_WAIT ? WAIT_LISTEN_MS : HOP_LISTEN_MS, start = node_ms();
    while (!paired && node.role == ROLE_AIR) {
        uint32_t spent = node_ms() - start;
        radio_rx_t *r = spent < listen_ms ? radio_recv(listen_ms - spent) : NULL;
        if (!r) {
            break;
        }
        int type = wire_type(r->data, r->len);
        wire_wait_t w;
        wire_admit_t a;
        if (type == WIRE_WAIT && wire_wait_get(r->data, r->len, &w) == 0 && memcmp(w.to, self, 6) == 0) {
            wire_air_wait(&st, ch, node_ms());
        } else if (type == WIRE_ADMIT && wire_admit_get(r->data, r->len, &a) == 0 && memcmp(a.to, self, 6) == 0) {
            admitted(r, &a);
        }
        radio_done(r);
    }
    return 0;
}

static uint32_t search_step(uint32_t now)
{
    uint8_t ch = wire_air_next(&st, now);
    if (ch == pair.home && st.tries == 1) {
        if (look(SEARCH_LOOK_MS) && exchange_with(parent, ch, HANDOVER_MS, true, true)) {
            return 0;
        }
        to_hub();
    }
    if (exchange_with(pair.hub, ch, HANDOVER_MS, true, false)) {
        to_hub();
        last_answer = node_ms();
        if (ch != pair.home) {
            pair.home = ch;
            node_save("air", &pair, sizeof(pair));
        }
        return 0;
    }
    return SEARCH_PAUSE_MS;
}

static void air_task(void *arg)
{
    for (;;) {
        if (node.role != ROLE_AIR) {
            led_blink(BLINK_OFF);
            ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1000));
            continue;
        }
        uint32_t now = node_ms(), wait = 100;
        if (reboot_pending && (empty() || (int32_t)(now - reboot_at) >= 0)) {
            esp_restart();
        }
        xSemaphoreTake(radio_lock, portMAX_DELAY);
        if (!paired) {
            led_blink(BLINK_FAST);
            wait = join_step(now);
        } else if (st.state == AIR_LINKED) {
            led_blink(BLINK_OFF);
            if (wire_air_lost(&st, now)) {
                node_run(false);
                drop_kids();
                to_hub();
                uplink_log("wireless: no hub for %u s, stopped", (unsigned)(WIRE_SILENCE_MS / 1000));
            } else if (!node.running) {
                wait = 0;
                if (now - last_poll >= POLL_IDLE_MS) {
                    last_poll = now;
                    if (handover(true)) {
                        led_pulse();
                    }
                }
                if (can_relay(node_ms()) && radio_channel(st.home)) {
                    listen(IDLE_LISTEN_MS);
                    tend(node_ms());
                } else {
                    wait = IDLE_LISTEN_MS;
                }
            }
        } else {
            led_blink(BLINK_SLOW);
            wait = search_step(now);
        }
        xSemaphoreGive(radio_lock);
        if (wait) {
            ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(wait));
        }
    }
}

void air_init(size_t cap)
{
    out_cap = cap;
    out_lock = xSemaphoreCreateMutex();
    radio_lock = xSemaphoreCreateMutex();
    assert(out_lock && radio_lock);
    paired = node_load("air", &pair, sizeof(pair)) == sizeof(pair);
    session = esp_random();
    wire_air_init(&st, paired, pair.home);
}

void air_begin(void)
{
    if (!out.buf) {
        uint8_t *buf = heap_caps_get_total_size(MALLOC_CAP_SPIRAM) ? heap_caps_malloc(out_cap, MALLOC_CAP_SPIRAM)
                                                                    : heap_caps_malloc(out_cap, MALLOC_CAP_INTERNAL);
        if (!buf) {
            uplink_log("wireless: no memory");
            return;
        }
        wire_out_init(&out, buf, out_cap, 0);
    }
    if (!task && xTaskCreate(air_task, "air", 6144, NULL, 5, &task) != pdPASS) {
        task = NULL;
        uplink_log("wireless: no memory");
        return;
    }
    if (!radio_start()) {
        return;
    }
    if (node.running) {
        node_run(false);
    }
    xSemaphoreTake(radio_lock, portMAX_DELAY);
    radio_mac(self);
    wire_air_init(&st, paired, pair.home);
    drop_kids();
    if (paired) {
        link_hub();
        to_hub();
    }
    last_poll = 0;
    last_answer = node_ms();
    last_hub_try = node_ms();
    hub_run = -1;
    node.role = ROLE_AIR;
    xSemaphoreGive(radio_lock);
    xTaskNotifyGive(task);
}

void air_end(void)
{
    if (node.role != ROLE_AIR) {
        return;
    }
    node.role = ROLE_USB;
    node_run(false);
    xSemaphoreTake(radio_lock, portMAX_DELAY);
    drop_kids();
    if (paired) {
        to_hub();
    }
    xSemaphoreGive(radio_lock);
    radio_stop();
    led_blink(BLINK_OFF);
}

bool air_line(const char *line, size_t n)
{
    if (n < 5 || line[n - 4] != '*') {
        return false;
    }
    xSemaphoreTake(out_lock, portMAX_DELAY);
    bool ok = out.buf && wire_out_add(&out, node_ms(), line, n - 4) == 0;
    xSemaphoreGive(out_lock);
    if (!ok) {
        __atomic_add_fetch(&node.dropped, 1, __ATOMIC_RELAXED);
    }
    return ok;
}

void air_handover(void)
{
    if (node.role != ROLE_AIR || !paired || xSemaphoreTake(radio_lock, pdMS_TO_TICKS(200)) != pdTRUE) {
        return;
    }
    if (st.state == AIR_LINKED) {
        handovers++;
        if (handover(false)) {
            led_pulse();
        }
        window();
    }
    last_poll = node_ms();
    xSemaphoreGive(radio_lock);
}

bool air_linked(void)
{
    return node.role == ROLE_AIR && paired && st.state == AIR_LINKED;
}

void air_unpair(void)
{
    xSemaphoreTake(radio_lock, portMAX_DELAY);
    unpair();
    xSemaphoreGive(radio_lock);
    if (task) {
        xTaskNotifyGive(task);
    }
}

void air_reboot(void)
{
    reboot_at = node_ms() + REBOOT_WAIT_MS;
    reboot_pending = true;
}
