#include <string.h>

#include "esp_heap_caps.h"
#include "host/ble_gap.h"
#include "host/ble_hs.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"

#include "node.h"
#include "seen.h"

#define SCAN_ITVL 0x50
#define INFO_NAMED 1
#define INFO_MFR 2
#define MIN_HEAP (56 * 1024)

static seen_t seen;
static uint32_t gen;
static volatile bool inited, synced, want;

static uint8_t flags_of(uint8_t type)
{
    switch (type) {
    case BLE_HCI_ADV_RPT_EVTYPE_ADV_IND:
        return BF_CONN | BF_SCAN;
    case BLE_HCI_ADV_RPT_EVTYPE_DIR_IND:
        return BF_CONN;
    case BLE_HCI_ADV_RPT_EVTYPE_SCAN_IND:
        return BF_SCAN;
    case BLE_HCI_ADV_RPT_EVTYPE_SCAN_RSP:
        return BF_SCAN | BF_RSP;
    default:
        return 0;
    }
}

static void heard(const struct ble_gap_disc_desc *d)
{
    if (!node.running || d->rssi == 127) {
        return;
    }
    if (gen != node.gen) {
        gen = node.gen;
        seen_clear(&seen);
        node.devices = 0;
    }
    proto_adv_t a;
    proto_adv_parse(d->data, d->length_data, &a);
    uint8_t type = d->addr.type & 1;
    uint8_t key[SEEN_KEY] = {0};
    memcpy(key, d->addr.val, 6);
    key[6] = type;
    uint32_t now = node_ms();
    uint8_t info = (a.name_len ? INFO_NAMED : 0) | (a.has_mfr ? INFO_MFR : 0);
    if (seen_check(&seen, key, d->rssi, now, info) == SEEN_SKIP) {
        return;
    }
    proto_ble_t b = {
        .addr_type = type,
        .rssi = d->rssi,
        .flags = flags_of(d->event_type),
        .has_mfr = a.has_mfr,
        .mfr = a.mfr,
        .name_len = a.name_len,
    };
    memcpy(b.addr, d->addr.val, 6);
    memcpy(b.name, a.name, a.name_len);
    if (uplink_ble(&b, now, 0)) {
        seen_note(&seen, key, d->rssi, now, info);
        node.devices = seen.added;
    }
}

static int start(void);

static int on_gap(struct ble_gap_event *e, void *arg)
{
    if (e->type == BLE_GAP_EVENT_DISC) {
        heard(&e->disc);
    } else if (e->type == BLE_GAP_EVENT_DISC_COMPLETE && want) {
        start();
    }
    return 0;
}

static int start(void)
{
    struct ble_gap_disc_params p = {
        .itvl = SCAN_ITVL,
        .window = SCAN_ITVL,
        .filter_duplicates = 0,
        .passive = 0,
    };
    if (ble_gap_disc_active()) {
        return 0;
    }
    int rc = ble_gap_disc(BLE_OWN_ADDR_RANDOM, BLE_HS_FOREVER, &p, on_gap, NULL);
    if (rc) {
        uplink_log("ble scan failed: %d", rc);
    }
    return rc;
}

static void on_sync(void)
{
    ble_addr_t addr;
    if (ble_hs_id_gen_rnd(1, &addr) == 0) {
        ble_hs_id_set_rnd(addr.val);
    }
    synced = true;
    if (want) {
        start();
    }
}

static void on_reset(int reason)
{
    synced = false;
}

static void host_task(void *arg)
{
    nimble_port_run();
    nimble_port_freertos_deinit();
}

void ble_set(bool on)
{
    want = on;
    if (on && !inited) {
        if (heap_caps_get_free_size(MALLOC_CAP_INTERNAL) < MIN_HEAP || seen_init(&seen, NODE_BLE_MAX)) {
            uplink_log("ble: no memory");
            node.no_ble = true;
            want = false;
            return;
        }
        esp_err_t err = nimble_port_init();
        if (err != ESP_OK) {
            uplink_log("ble init failed: %s", esp_err_to_name(err));
            seen_free(&seen);
            node.no_ble = true;
            want = false;
            return;
        }
        gen = node.gen - 1;
        ble_hs_cfg.sync_cb = on_sync;
        ble_hs_cfg.reset_cb = on_reset;
        inited = true;
        node.no_ble = false;
        nimble_port_freertos_init(host_task);
        return;
    }
    if (!synced) {
        return;
    }
    if (on) {
        start();
    } else if (ble_gap_disc_active()) {
        ble_gap_disc_cancel();
    }
}
