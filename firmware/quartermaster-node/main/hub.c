#include <stdio.h>
#include <string.h>

#include "esp_random.h"

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "enow.h"
#include "node.h"

#define REPORT_MS 5000
#define ADMIT_KEEP_MS 120000u
#define LEAVE_KEEP_MS 60000u
#define ADMIT_WINDOW_S 30
#define ADMIT_FRAME 112
#define JC_HELLO_MS 10000u

typedef struct {
    uint8_t used, len;
    uint8_t mac[6];
    uint8_t pub[WIRE_PUB];
    uint8_t frame[ADMIT_FRAME];
    uint32_t since;
} admit_t;

typedef struct {
    uint8_t used, state, kind, heard_ok, ver, lo, hi, has_plan;
    uint8_t mac[6];
    int8_t rssi;
    char country[3];
    uint32_t heard, since, frames, seq, plan, wifi, ble, said;
} jc_t;

static SemaphoreHandle_t lock;
static TaskHandle_t task;
static uint8_t key[WIRE_KEY], home = WIRE_HOME_DEFAULT, self[6];
static wire_peer_t peers[WIRE_SLOTS];
static wire_pair_t pairing;
static admit_t admits[WIRE_NODES];
static uint32_t relayed;
static uint8_t frame[WIRE_FRAME_MAX];
static char line[PROTO_LINE_MAX + 1];
static jc_t jcs[WIRE_NODES];
static uint8_t jc_ver, jc_frame_buf[ENOW_MSG_LEN];
static char jc_text_buf[PROTO_LINE_MAX + 1], jc_wrap[PROTO_LINE_MAX + 1];

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
    for (unsigned i = 0; i < WIRE_NODES; i++) {
        n += jcs[i].used;
    }
    return n;
}

static jc_t *jc_find(const uint8_t mac[6])
{
    for (unsigned i = 0; i < WIRE_NODES; i++) {
        if (jcs[i].used && memcmp(jcs[i].mac, mac, 6) == 0) {
            return &jcs[i];
        }
    }
    return NULL;
}

static const char *jc_fw(int kind)
{
    return kind == ENOW_KIND_PIGLET ? "piglet" : kind == ENOW_KIND_6G ? "jcmk6g" : "jcmk";
}

static int jc_kind(const char *fw)
{
    return !strcmp(fw, "piglet") ? ENOW_KIND_PIGLET : !strcmp(fw, "jcmk6g") ? ENOW_KIND_6G
                                                      : !strcmp(fw, "jcmk") ? ENOW_KIND_JCMK : -1;
}

static void jc_save(void)
{
    uint8_t list[WIRE_NODES * 7];
    size_t n = 0;
    for (unsigned i = 0; i < WIRE_NODES; i++) {
        if (jcs[i].used) {
            memcpy(list + n, jcs[i].mac, 6);
            list[n + 6] = jcs[i].kind;
            n += 7;
        }
    }
    if (n) {
        node_save("hub_jcmk", list, n);
    } else {
        node_erase("hub_jcmk");
    }
}

static void jc_load(uint32_t now)
{
    uint8_t list[WIRE_NODES * 7];
    size_t n = node_load("hub_jcmk", list, sizeof(list));
    for (size_t i = 0; i + 7 <= n && i / 7 < WIRE_NODES; i += 7) {
        jc_t *j = &jcs[i / 7];
        memset(j, 0, sizeof(*j));
        j->used = 1;
        memcpy(j->mac, list + i, 6);
        j->kind = list[i + 6] <= ENOW_KIND_6G ? list[i + 6] : ENOW_KIND_JCMK;
        j->state = PEER_UP;
        j->since = now;
    }
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
    memset(jcs, 0, sizeof(jcs));
    wire_pair_open(&pairing, node_ms(), 0);
}

static wire_peer_t *slot(void)
{
    wire_peer_t *leaving = NULL, *free = NULL;
    unsigned used = 0;
    for (unsigned i = 0; i < WIRE_SLOTS; i++) {
        if (peers[i].state == PEER_FREE) {
            free = free ? free : &peers[i];
            continue;
        }
        used++;
        if (peers[i].state == PEER_LEAVING && (!leaving || (int32_t)(peers[i].since - leaving->since) < 0)) {
            leaving = &peers[i];
        }
    }
    if (free && used < WIRE_NODES) {
        return free;
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
static void shown(wire_peer_t *p, uint32_t now);
static wire_peer_t *root_of(wire_peer_t *p, unsigned *hops);

static void jc_line(jc_t *j, const char *l, int n)
{
    if (n <= 0) {
        return;
    }
    const char *star = memchr(l, '*', n);
    int m = wire_relay(jc_wrap, sizeof(jc_wrap), j->mac, l, star ? (size_t)(star - l) : (size_t)n, 0);
    if (m <= 0) {
        return;
    }
    if (!uplink_raw_fits(WIRE_RING_ITEM(m))) {
        __atomic_add_fetch(&node.dropped, 1, __ATOMIC_RELAXED);
        return;
    }
    uplink_raw(jc_wrap, m);
    relayed++;
}

static void jc_hello(jc_t *j, uint32_t now)
{
    proto_hello_t h = {
        .fw = jc_fw(j->kind),
        .board = "c5",
        .caps = j->kind == ENOW_KIND_6G ? "wifi6+block" : "wifi24+wifi5+block",
        .plan = j->plan,
        .running = node.running,
    };
    wire_hexid(j->mac, h.id);
    memcpy(h.country, j->country[0] ? j->country : "01", 3);
    jc_line(j, jc_text_buf, proto_hello(jc_text_buf, sizeof(jc_text_buf), &h));
    j->said = now;
}

static void jc_status(jc_t *j, uint32_t now)
{
    proto_status_t st = {
        .uptime = now - j->since,
        .networks = j->wifi,
        .devices = j->ble,
        .plan = j->plan,
        .running = node.running,
    };
    jc_line(j, jc_text_buf, proto_status(jc_text_buf, sizeof(jc_text_buf), j->seq++, &st));
}

static void jc_ack(jc_t *j, const char *cmd, int ok, const char *detail)
{
    jc_line(j, jc_text_buf, proto_ack(jc_text_buf, sizeof(jc_text_buf), j->seq++, cmd, ok, detail));
}

static void jc_node(jc_t *j, uint32_t now)
{
    wire_node_t n = {
        .state = j->state,
        .heard_ok = j->heard_ok,
        .rssi = j->rssi,
        .ago = j->heard_ok ? now - j->heard : 0,
        .frames = j->frames,
        .hops = 1,
    };
    memcpy(n.mac, j->mac, 6);
    uplink_node(&n);
}

static void jc_admin(jc_t *j)
{
    if (!j->has_plan || j->kind == ENOW_KIND_6G) {
        return;
    }
    enow_admin_t a = {.version = j->ver, .start = j->lo, .end = j->hi};
    for (unsigned i = 0; i < WIRE_NODES; i++) {
        if (jcs[i].used && jcs[i].has_plan && jcs[i].kind != ENOW_KIND_6G) {
            a.index += &jcs[i] < j;
            a.count++;
        }
    }
    int n = enow_admin_put(jc_frame_buf, sizeof(jc_frame_buf), &a);
    radio_send_plain(j->mac, jc_frame_buf, n);
}

static void jc_plan(jc_t *j, const proto_cmd_t *c)
{
    proto_plan_t p;
    char ver[12];
    const char *err = proto_plan(c, &p);
    if (err) {
        jc_ack(j, "PLAN", 0, err);
        return;
    }
    uint8_t lo = 0, hi = ENOW_CHANNELS - 1;
    enow_range(p.ch2, p.ch5, &lo, &hi);
    if (!j->has_plan || lo != j->lo || hi != j->hi) {
        if (!++jc_ver) {
            jc_ver = 1;
        }
        j->ver = jc_ver;
        j->lo = lo;
        j->hi = hi;
        j->has_plan = 1;
    }
    j->plan = p.version;
    memcpy(j->country, p.country, 3);
    snprintf(ver, sizeof(ver), "%lu", (unsigned long)p.version);
    jc_ack(j, "PLAN", 1, ver);
}

static void jc_cmd(jc_t *j, const char *cmd, uint32_t now)
{
    proto_cmd_t c;
    int n = snprintf(jc_text_buf, sizeof(jc_text_buf), "#%s", cmd);
    if (n <= 0 || (size_t)n >= sizeof(jc_text_buf) || proto_parse(jc_text_buf, n, &c) != PROTO_CMD) {
        return;
    }
    if (!strcmp(c.name, "HELLO")) {
        jc_ack(j, c.name, 1, "");
        jc_hello(j, now);
    } else if (!strcmp(c.name, "PLAN")) {
        jc_plan(j, &c);
    } else {
        jc_ack(j, c.name, 0, "jcmk");
    }
}

static void jc_text(jc_t *j, const enow_msg_t *m)
{
    enow_rec_t rec;
    int kind = enow_record(m->text, m->len, &rec);
    if (kind < 0 || !node.running) {
        return;
    }
    int n;
    if (kind == ENOW_REC_WIFI) {
        n = enow_wifi_line(jc_text_buf, sizeof(jc_text_buf), j->seq++, 0, &rec);
        j->wifi++;
    } else {
        n = proto_ble(jc_text_buf, sizeof(jc_text_buf), j->seq++, 0, &rec.ble);
        j->ble++;
    }
    jc_line(j, jc_text_buf, n);
}

static void jc_candidate(const radio_rx_t *r, const enow_msg_t *m, uint32_t now)
{
    wire_join_t jn;
    wire_cand_t *known = wire_pair_find(&pairing, r->mac, now);
    if (m->type == ENOW_CORE_REQUEST || !known || jc_kind(known->join.fw) < 0) {
        memset(&jn, 0, sizeof(jn));
        strcpy(jn.fw, jc_fw(m->type == ENOW_CORE_REQUEST ? enow_kind(m) : ENOW_KIND_JCMK));
        strcpy(jn.board, "c5");
    } else {
        jn = known->join;
    }
    int tell;
    wire_cand_t *c = wire_pair_heard(&pairing, r->mac, r->rssi, &jn, now, &tell);
    if (c && tell) {
        wire_joiner_t j = {.rssi = r->rssi};
        memcpy(j.mac, r->mac, 6);
        memcpy(j.fw, jn.fw, sizeof(j.fw));
        memcpy(j.board, jn.board, sizeof(j.board));
        uplink_join(&j);
    }
}

static void jc_frame(const radio_rx_t *r, uint32_t now)
{
    enow_msg_t m;
    if (enow_get(r->data, r->len, &m)) {
        return;
    }
    jc_t *j = jc_find(r->mac);
    if (m.type != ENOW_CORE_REQUEST && m.type != ENOW_HEARTBEAT && m.type != ENOW_TEXT) {
        return;
    }
    if (!j) {
        if (m.type != ENOW_TEXT) {
            jc_candidate(r, &m, now);
        }
        return;
    }
    bool fresh = j->state == PEER_NEW;
    j->heard = now;
    j->heard_ok = 1;
    j->rssi = r->rssi;
    j->state = PEER_UP;
    if (fresh) {
        jc_node(j, now);
        jc_hello(j, now);
    }
    if (m.type == ENOW_CORE_REQUEST) {
        int n = enow_put(jc_frame_buf, sizeof(jc_frame_buf), ENOW_CORE_REPLY, 0, NULL);
        radio_send_plain(j->mac, jc_frame_buf, n);
        jc_admin(j);
    } else if (m.type == ENOW_HEARTBEAT) {
        jc_admin(j);
    } else {
        j->frames++;
        jc_text(j, &m);
    }
}

static const char *jc_admit(const uint8_t mac[6], const wire_cand_t *c, uint32_t now)
{
    jc_t *j = jc_find(mac);
    for (unsigned i = 0; i < WIRE_NODES && !j; i++) {
        if (!jcs[i].used) {
            if (members() >= WIRE_NODES) {
                return "full";
            }
            j = &jcs[i];
            memset(j, 0, sizeof(*j));
            j->used = 1;
            memcpy(j->mac, mac, 6);
            j->kind = jc_kind(c->join.fw);
            j->state = PEER_NEW;
            j->since = now;
        }
    }
    if (!j) {
        return "full";
    }
    jc_save();
    if (wire_pair_left(&pairing, now) < ADMIT_WINDOW_S) {
        wire_pair_open(&pairing, now, ADMIT_WINDOW_S);
    }
    return NULL;
}

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
            if (peers[i].has_via && !root_of(&peers[i], NULL)) {
                peers[i].has_via = 0;
                peers[i].via_ok = 0;
            }
            shown(&peers[i], now);
        }
    }
    for (unsigned i = 0; i < WIRE_NODES; i++) {
        jc_t *j = &jcs[i];
        if (!j->used) {
            continue;
        }
        jc_node(j, now);
        if (j->heard_ok && now - j->heard < WIRE_SILENCE_MS) {
            if (now - j->said >= JC_HELLO_MS) {
                jc_hello(j, now);
            }
            if (node.running) {
                jc_status(j, now);
            }
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
        node_erase("hub_jcmk");
    } else {
        jc_load(node_ms());
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
    if (c && jc_kind(c->join.fw) >= 0 && !p) {
        err = jc_admit(mac, c, now);
    } else if (!c) {
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
    jc_t *j = jc_find(mac);
    if (j) {
        memset(j, 0, sizeof(*j));
        jc_save();
        xSemaphoreGive(lock);
        return NULL;
    }
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
    jc_t *j = jc_find(mac);
    const char *err = NULL;
    if (j) {
        jc_cmd(j, cmd, node_ms());
    } else {
        err = !p || !member(p) ? "node" : wire_peer_cmd(p, cmd, strlen(cmd)) ? "busy" : NULL;
    }
    xSemaphoreGive(lock);
    return err;
}

static wire_peer_t *via_of(const wire_peer_t *p)
{
    if (!p->has_via) {
        return NULL;
    }
    wire_peer_t *v = find(p->via);
    return v && member(v) && v != p ? v : NULL;
}

static wire_peer_t *root_of(wire_peer_t *p, unsigned *hops)
{
    unsigned n = 1;
    while (p && p->has_via && n < WIRE_HOPS) {
        p = via_of(p);
        n++;
    }
    if (hops) {
        *hops = n;
    }
    return p && !p->has_via ? p : NULL;
}

static void shown(wire_peer_t *p, uint32_t now)
{
    unsigned hops = 1;
    wire_node_t nd;
    wire_peer_node(p, now, &nd);
    if (p->has_via && root_of(p, &hops)) {
        nd.hops = hops;
    }
    uplink_node(&nd);
}

static void heard_via(wire_peer_t *by, const char *l, size_t len, uint32_t now)
{
    wire_via_t v;
    if (wire_via_get(l, len, &v)) {
        return;
    }
    wire_peer_t *k = find(v.id);
    if (!k || !member(k) || k == by) {
        return;
    }
    if (k->heard_ok && !k->has_via && now - k->heard <= v.ago) {
        return;
    }
    bool fresh = k->state == PEER_NEW;
    wire_peer_via(k, by->mac, &v, now);
    if (!root_of(k, NULL)) {
        k->has_via = 0;
        k->via_ok = 0;
        return;
    }
    if (fresh) {
        drop_admit(k->mac);
        shown(k, now);
    }
}

static void relay(wire_peer_t *p, const wire_data_t *d, uint32_t now)
{
    size_t pos = 0, len, ilen;
    uint32_t stamp;
    const char *l, *inner;
    uint8_t who[6];
    while (wire_rec_next(d, &pos, &stamp, &l, &len)) {
        wire_peer_t *from = p;
        if (wire_inner(l, len, who, &inner, &ilen)) {
            from = find(who);
            if (!from || !member(from) || from == p) {
                continue;
            }
        } else {
            inner = l;
            ilen = len;
        }
        if (ilen > 3 && strncmp(inner, "@V,", 3) == 0) {
            heard_via(from, inner, ilen, now);
            continue;
        }
        int n = wire_relay(line, sizeof(line), p->mac, l, len, d->now - stamp);
        if (n > 0) {
            uplink_raw(line, n);
            relayed++;
        }
    }
}

static int carry_for(wire_peer_t *p, int n)
{
    n = wire_ack_relay(frame, radio_max(), n, 0);
    for (unsigned i = 0; i < WIRE_SLOTS && n > 0; i++) {
        wire_peer_t *q = &peers[i];
        if (q == p || q->state == PEER_FREE || !q->has_via || root_of(q, NULL) != p) {
            continue;
        }
        uint8_t kind = q->state == PEER_LEAVING ? WC_BYE : WC_CMDS;
        int m = wire_ack_carry(frame, radio_max(), n, q->mac, kind, q->cmd, kind == WC_BYE ? 0 : q->ncmd);
        if (m < 0) {
            break;
        }
        n = m;
    }
    return n;
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
    p->has_via = 0;
    p->via_ok = 0;
    int forward = wire_peer_data(p, &d, r->rssi, now);
    int n = wire_ack_put(frame, radio_max(), p, d.session, d.seq, flags);
    if (d.flags & WD_RELAY) {
        n = carry_for(p, n);
    }
    if (n > 0) {
        radio_send(p->mac, frame, n);
    }
    if (forward) {
        relay(p, &d, now);
    }
    if (fresh) {
        drop_admit(p->mac);
        shown(p, now);
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
            } else if (r->len >= 5 && memcmp(r->data, "ENOW", 4) == 0) {
                jc_frame(r, now);
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
    jc_ver = esp_random();
    lock = xSemaphoreCreateMutex();
    assert(lock);
}
