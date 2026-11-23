#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "node.h"

#define REPORT_MS 5000
#define ADMIT_KEEP_MS 120000u
#define LEAVE_KEEP_MS 60000u
#define ADMIT_WINDOW_S 30
#define ADMIT_FRAME 112

typedef struct {
    uint8_t used, len;
    uint8_t mac[6];
    uint8_t pub[WIRE_PUB];
    uint8_t frame[ADMIT_FRAME];
    uint32_t since;
} admit_t;

static SemaphoreHandle_t lock;
static TaskHandle_t task;
static uint8_t key[WIRE_KEY], home = WIRE_HOME_DEFAULT, self[6];
static wire_peer_t peers[WIRE_SLOTS];
static wire_pair_t pairing;
static admit_t admits[WIRE_NODES];
static uint32_t relayed;
static uint8_t frame[WIRE_FRAME_MAX];
static char line[PROTO_LINE_MAX + 1];

static bool member(const wire_peer_t *p)
{
    return p->state == PEER_NEW || p->state == PEER_UP;
}

static wire_peer_t *find(const uint8_t mac[6])
{
    for (unsigned i = 0; i < WIRE_SLOTS; i++) {
        if (peers[i].state != PEER_FREE && memcmp(peers[i].mac, mac, 6) == 0) {
            return &peers[i];
        }
    }
    return NULL;
}

static unsigned members(void)
{
    unsigned n = 0;
    for (unsigned i = 0; i < WIRE_SLOTS; i++) {
        n += member(&peers[i]);
    }
    return n;
}

static void save_nodes(void)
{
    uint8_t list[WIRE_NODES * 6];
    size_t n = 0;
    for (unsigned i = 0; i < WIRE_SLOTS && n < sizeof(list); i++) {
        if (member(&peers[i])) {
            memcpy(list + n, peers[i].mac, 6);
            n += 6;
        }
    }
    if (n) {
        node_save("hub_nodes", list, n);
    } else {
        node_erase("hub_nodes");
    }
}

static void release(wire_peer_t *p)
{
    radio_unpeer(p->mac);
    memset(p, 0, sizeof(*p));
}

static void clear(void)
{
    for (unsigned i = 0; i < WIRE_SLOTS; i++) {
        if (peers[i].state != PEER_FREE) {
            release(&peers[i]);
        }
    }
    memset(admits, 0, sizeof(admits));
    wire_pair_open(&pairing, node_ms(), 0);
}

static wire_peer_t *slot(void)
{
    wire_peer_t *leaving = NULL;
    for (unsigned i = 0; i < WIRE_SLOTS; i++) {
        if (peers[i].state == PEER_FREE) {
            return &peers[i];
        }
        if (peers[i].state == PEER_LEAVING && (!leaving || (int32_t)(peers[i].since - leaving->since) < 0)) {
            leaving = &peers[i];
        }
    }
    if (leaving) {
        release(leaving);
    }
    return leaving;
}

static admit_t *admit_for(const uint8_t mac[6], uint32_t now)
{
    for (unsigned i = 0; i < WIRE_NODES; i++) {
        admit_t *a = &admits[i];
        if (a->used && memcmp(a->mac, mac, 6) == 0) {
            if (now - a->since < ADMIT_KEEP_MS) {
                return a;
            }
            a->used = 0;
        }
    }
    return NULL;
}

static void drop_admit(const uint8_t mac[6])
{
    for (unsigned i = 0; i < WIRE_NODES; i++) {
        if (admits[i].used && memcmp(admits[i].mac, mac, 6) == 0) {
            admits[i].used = 0;
        }
    }
}

static bool make_admit(admit_t *a, const uint8_t mac[6], const uint8_t pub[WIRE_PUB])
{
    wire_admit_t w = {.channel = home};
    uint8_t kek[32];
    memcpy(w.to, mac, 6);
    bool ok = radio_kp_new(w.pub) && radio_kek(pub, mac, self, kek);
    radio_kp_free();
    if (!ok) {
        return false;
    }
    radio_wrap(kek, key, w.wrapped, w.check);
    memset(kek, 0, sizeof(kek));
    int n = wire_admit_put(a->frame, sizeof(a->frame), &w);
    if (n < 0) {
        return false;
    }
    a->len = n;
    memcpy(a->mac, mac, 6);
    memcpy(a->pub, pub, WIRE_PUB);
    a->used = 1;
    return true;
}

static void hub_task(void *arg);

static void report(uint32_t now)
{
    wire_cluster_t c = {
        .home = home,
        .left = wire_pair_left(&pairing, now),
        .nodes = members(),
        .running = node.running,
        .relayed = relayed,
        .dropped = node.dropped,
    };
    uplink_cluster(&c);
    for (unsigned i = 0; i < WIRE_SLOTS; i++) {
        if (member(&peers[i])) {
            wire_node_t n;
            wire_peer_node(&peers[i], now, &n);
            uplink_node(&n);
        }
    }
}

void hub_report(void)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    report(node_ms());
    xSemaphoreGive(lock);
}

const char *hub_begin(uint8_t ch, const uint8_t k[WIRE_KEY])
{
    if (!radio_start()) {
        return "radio";
    }
    if (node.running && node.role != ROLE_HUB) {
        node_run(false);
    }
    if (!task && xTaskCreate(hub_task, "hub", 6144, NULL, 5, &task) != pdPASS) {
        task = NULL;
        return "memory";
    }
    bool tuned = false;
    for (int i = 0; i < 10 && !(tuned = radio_channel(ch)); i++) {
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    if (!tuned) {
        return "channel";
    }
    xSemaphoreTake(lock, portMAX_DELAY);
    uint8_t stored[WIRE_KEY];
    bool same = node_load("hub_key", stored, sizeof(stored)) == WIRE_KEY && memcmp(stored, k, WIRE_KEY) == 0;
    clear();
    memcpy(key, k, WIRE_KEY);
    home = ch;
    radio_mac(self);
    radio_pmk(key);
    if (!same) {
        node_save("hub_key", key, WIRE_KEY);
        node_erase("hub_nodes");
    } else {
        uint8_t list[WIRE_NODES * 6];
        size_t n = node_load("hub_nodes", list, sizeof(list));
        for (size_t i = 0; i + 6 <= n; i += 6) {
            wire_peer_t *p = &peers[i / 6];
            wire_peer_init(p, list + i, PEER_UP, node_ms());
            if (!radio_peer(p->mac, key, p->mac)) {
                memset(p, 0, sizeof(*p));
            }
        }
    }
    node.role = ROLE_HUB;
    xSemaphoreGive(lock);
    xTaskNotifyGive(task);
    return NULL;
}

void hub_end(void)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    clear();
    node.role = ROLE_USB;
    radio_stop();
    xSemaphoreGive(lock);
}

const char *hub_pair(unsigned seconds)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    wire_pair_open(&pairing, node_ms(), seconds);
    xSemaphoreGive(lock);
    return NULL;
}

const char *hub_admit(const uint8_t mac[6])
{
    const char *err = NULL;
    uint32_t now = node_ms();
    xSemaphoreTake(lock, portMAX_DELAY);
    wire_peer_t *p = find(mac);
    wire_cand_t *c = wire_pair_find(&pairing, mac, now);
    admit_t *a = admit_for(mac, now);
    for (unsigned i = 0; i < WIRE_NODES && !a; i++) {
        if (!admits[i].used) {
            a = &admits[i];
        }
    }
    if (!a) {
        a = &admits[0];
        for (unsigned i = 1; i < WIRE_NODES; i++) {
            if ((int32_t)(admits[i].since - a->since) < 0) {
                a = &admits[i];
            }
        }
    }
    if (!c) {
        err = "node";
    } else if (!p && members() >= WIRE_NODES) {
        err = "full";
    } else if (!make_admit(a, mac, c->join.pub)) {
        err = "radio";
    } else {
        a->since = now;
        if (!p || p->state == PEER_LEAVING) {
            if (p) {
                release(p);
            }
            p = slot();
            if (!p) {
                err = "full";
            } else {
                wire_peer_init(p, mac, PEER_NEW, now);
                if (!radio_peer(mac, key, mac)) {
                    memset(p, 0, sizeof(*p));
                    err = "radio";
                }
            }
        }
        if (err) {
            a->used = 0;
        } else {
            save_nodes();
            if (wire_pair_left(&pairing, now) < ADMIT_WINDOW_S) {
                wire_pair_open(&pairing, now, ADMIT_WINDOW_S);
            }
            radio_send(NULL, a->frame, a->len);
        }
    }
    xSemaphoreGive(lock);
    return err;
}

const char *hub_forget(const uint8_t mac[6])
{
    xSemaphoreTake(lock, portMAX_DELAY);
    wire_peer_t *p = find(mac);
    if (!p || !member(p)) {
        xSemaphoreGive(lock);
        return "node";
    }
    p->state = PEER_LEAVING;
    p->since = node_ms();
    p->ncmd = 0;
    drop_admit(mac);
    save_nodes();
    int n = wire_bye_put(frame, sizeof(frame));
    radio_send(mac, frame, n);
    xSemaphoreGive(lock);
    return NULL;
}

const char *hub_to(const uint8_t mac[6], const char *cmd)
{
    xSemaphoreTake(lock, portMAX_DELAY);
    wire_peer_t *p = find(mac);
    const char *err = !p || !member(p) ? "node" : wire_peer_cmd(p, cmd, strlen(cmd)) ? "busy" : NULL;
    xSemaphoreGive(lock);
    return err;
}

static void relay(const wire_peer_t *p, const wire_data_t *d)
{
    size_t pos = 0, len;
    uint32_t stamp;
    const char *l;
    while (wire_rec_next(d, &pos, &stamp, &l, &len)) {
        int n = wire_relay(line, sizeof(line), p->mac, l, len, d->now - stamp);
        if (n > 0) {
            uplink_raw(line, n);
            relayed++;
        }
    }
}

static void joined(const radio_rx_t *r, uint32_t now)
{
    wire_join_t j;
    if (wire_join_get(r->data, r->len, &j)) {
        return;
    }
    admit_t *a = admit_for(r->mac, now);
    if (a && memcmp(a->pub, j.pub, WIRE_PUB) == 0) {
        radio_send(NULL, a->frame, a->len);
        return;
    }
    int tell;
    wire_cand_t *c = wire_pair_heard(&pairing, r->mac, r->rssi, &j, now, &tell);
    if (!c) {
        return;
    }
    if (tell) {
        wire_joiner_t jn = {.rssi = r->rssi};
        memcpy(jn.mac, r->mac, 6);
        memcpy(jn.fw, j.fw, sizeof(jn.fw));
        memcpy(jn.board, j.board, sizeof(jn.board));
        uplink_join(&jn);
    }
    wire_wait_t w = {.channel = home, .seconds = wire_pair_left(&pairing, now)};
    memcpy(w.to, r->mac, 6);
    int n = wire_wait_put(frame, sizeof(frame), &w);
    radio_send(NULL, frame, n);
}

static void data(const radio_rx_t *r, uint32_t now)
{
    wire_peer_t *p = find(r->mac);
    wire_data_t d;
    if (!p || wire_data_get(r->data, r->len, &d)) {
        return;
    }
    uint8_t flags = (node.running ? WA_RUN : 0) | (d.flags & WD_POLL ? WA_POLL : 0);
    if (p->state == PEER_LEAVING) {
        int n = wire_ack_put(frame, sizeof(frame), NULL, d.session, d.seq, flags | WA_BYE);
        radio_send(p->mac, frame, n);
        return;
    }
    if (wire_peer_fresh(p, &d) && !uplink_raw_fits(wire_relay_need(p->mac, &d))) {
        d.flags |= WD_POLL;
        flags |= WA_POLL | WA_BUSY;
    }
    bool fresh = p->state == PEER_NEW;
    int forward = wire_peer_data(p, &d, r->rssi, now);
    int n = wire_ack_put(frame, radio_max(), p, d.session, d.seq, flags);
    radio_send(p->mac, frame, n);
    if (forward) {
        relay(p, &d);
    }
    if (fresh) {
        drop_admit(p->mac);
        wire_node_t nd;
        wire_peer_node(p, now, &nd);
        uplink_node(&nd);
    }
}

static void purge(uint32_t now)
{
    for (unsigned i = 0; i < WIRE_SLOTS; i++) {
        if (peers[i].state == PEER_LEAVING && now - peers[i].since >= LEAVE_KEEP_MS) {
            release(&peers[i]);
        }
    }
}

static void hub_task(void *arg)
{
    uint32_t last = 0;
    for (;;) {
        if (node.role != ROLE_HUB) {
            ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1000));
            continue;
        }
        radio_rx_t *r = radio_recv(100);
        uint32_t now = node_ms();
        xSemaphoreTake(lock, portMAX_DELAY);
        if (r && node.role == ROLE_HUB) {
            int type = wire_type(r->data, r->len);
            if (type == WIRE_JOIN) {
                joined(r, now);
            } else if (type == WIRE_DATA) {
                data(r, now);
            }
        }
        if (now - last >= REPORT_MS && node.role == ROLE_HUB) {
            last = now;
            purge(now);
            report(now);
        }
        xSemaphoreGive(lock);
        if (r) {
            radio_done(r);
        }
    }
}

void hub_init(void)
{
    lock = xSemaphoreCreateMutex();
    assert(lock);
}
