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

typedef struct {
    uint8_t hub[6];
    uint8_t key[WIRE_KEY];
    uint8_t home;
} pairing_t;

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
static uint8_t frame[WIRE_FRAME_MAX];
static char cmd[WIRE_CMD_MAX + 1];
static volatile bool reboot_pending;
static volatile uint32_t reboot_at;
static uint8_t answer;

static void link_hub(void)
{
    radio_pmk(pair.key);
    radio_peer(pair.hub, pair.key, self);
}

static void unpair(void)
{
    if (paired) {
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

static void acked(const wire_ack_t *a)
{
    xSemaphoreTake(out_lock, portMAX_DELAY);
    wire_out_ack(&out, a->seq, a->flags);
    xSemaphoreGive(out_lock);
    answer = a->flags;
    if (a->flags & WA_BYE) {
        unpair();
        uplink_log("wireless: hub let this node go");
        return;
    }
    wire_air_answer(&st, a->flags);
    for (unsigned i = 0; i < a->ncmd; i++) {
        if (wire_seen_fresh(&cmds, a->cmd[i].id)) {
            memcpy(cmd, a->cmd[i].line, a->cmd[i].len);
            cmd[a->cmd[i].len] = 0;
            node_remote(cmd, a->cmd[i].len);
        }
    }
}

static bool wait_ack(uint32_t ms)
{
    uint32_t start = node_ms();
    for (;;) {
        uint32_t spent = node_ms() - start;
        radio_rx_t *r = spent < ms ? radio_recv(ms - spent) : NULL;
        if (!r) {
            return false;
        }
        bool done = false;
        if (paired && memcmp(r->mac, pair.hub, 6) == 0) {
            wire_ack_t a;
            int type = wire_type(r->data, r->len);
            if (type == WIRE_ACK && wire_ack_get(r->data, r->len, &a) == 0 && a.session == session) {
                acked(&a);
                done = true;
            } else if (type == WIRE_BYE) {
                unpair();
                uplink_log("wireless: hub let this node go");
            }
        }
        radio_done(r);
        if (done || !paired) {
            return done;
        }
    }
}

static bool exchange(uint8_t ch, uint32_t budget, bool search)
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
            .flags = (cmds.have ? WD_CMD_ACK : 0) | (node.running ? WD_RUN : 0),
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
        bool delivered = n > 0 && radio_send(pair.hub, frame, n);
        if (delivered && wait_ack(ACK_WAIT_MS)) {
            heard = true;
            misses = 0;
            wire_air_linked(&st, ch, node_ms());
            if ((poll && !search) || (answer & WA_BUSY) || empty()) {
                break;
            }
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
    uint32_t listen = st.state == AIR_WAIT ? WAIT_LISTEN_MS : HOP_LISTEN_MS, start = node_ms();
    while (!paired && node.role == ROLE_AIR) {
        uint32_t spent = node_ms() - start;
        radio_rx_t *r = spent < listen ? radio_recv(listen - spent) : NULL;
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
                uplink_log("wireless: no hub for %u s, stopped", (unsigned)(WIRE_SILENCE_MS / 1000));
            } else if (!node.running && now - last_poll >= POLL_IDLE_MS) {
                last_poll = now;
                if (exchange(st.home, HANDOVER_MS, false)) {
                    led_pulse();
                }
            }
        } else {
            led_blink(BLINK_SLOW);
            uint8_t ch = wire_air_next(&st, now);
            if (exchange(ch, HANDOVER_MS, true)) {
                if (ch != pair.home) {
                    pair.home = ch;
                    node_save("air", &pair, sizeof(pair));
                }
            } else {
                wait = SEARCH_PAUSE_MS;
            }
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
    if (paired) {
        link_hub();
    }
    last_poll = 0;
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
    if (st.state == AIR_LINKED && exchange(st.home, HANDOVER_MS, false)) {
        led_pulse();
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
