#include <string.h>

#include "esp_event.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "node.h"
#include "seen.h"

#define RESULT_WAIT_MS 100

static SemaphoreHandle_t done;
static TaskHandle_t task;
static seen_t seen;

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (id == WIFI_EVENT_SCAN_DONE) {
        xSemaphoreGive(done);
    }
}

static uint16_t flags_of(const wifi_ap_record_t *r)
{
    return (r->ssid[0] ? 0 : WF_HIDDEN) | (r->wps ? WF_WPS : 0) | (r->phy_11b ? WF_11B : 0) |
           (r->phy_11g ? WF_11G : 0) | (r->phy_11n ? WF_11N : 0) | (r->phy_11a ? WF_11A : 0) |
           (r->phy_11ac ? WF_11AC : 0) | (r->phy_11ax ? WF_11AX : 0) | (r->ftm_responder ? WF_FTM : 0);
}

static void results(uint32_t t, uint32_t gen)
{
    wifi_ap_record_t r;
    TickType_t wait = pdMS_TO_TICKS(RESULT_WAIT_MS);
    while (esp_wifi_scan_get_ap_record(&r) == ESP_OK) {
        if (!node.running) {
            continue;
        }
        uint8_t key[SEEN_KEY] = {0};
        memcpy(key, r.bssid, 6);
        uint32_t now = node_ms();
        if (seen_check(&seen, key, r.rssi, now, 0) == SEEN_SKIP) {
            continue;
        }
        proto_wifi_t w = {
            .rssi = r.rssi,
            .channel = r.primary,
            .auth = r.authmode,
            .pairwise = r.pairwise_cipher,
            .group = r.group_cipher,
            .flags = flags_of(&r),
        };
        memcpy(w.bssid, r.bssid, 6);
        w.ssid_len = strnlen((const char *)r.ssid, sizeof(w.ssid));
        memcpy(w.ssid, r.ssid, w.ssid_len);
        if (uplink_wifi(&w, t, wait)) {
            seen_note(&seen, key, r.rssi, now, 0);
        } else {
            wait = 0;
        }
    }
    esp_wifi_clear_ap_list();
    if (gen == node.gen) {
        node.networks = seen.added;
    }
}

static void part(const proto_plan_t *p, uint16_t c2, uint32_t c5, bool passive, uint32_t gen)
{
    if (!c2 && !c5) {
        return;
    }
    wifi_scan_config_t cfg;
    memset(&cfg, 0, sizeof(cfg));
    cfg.show_hidden = true;
    cfg.scan_type = passive ? WIFI_SCAN_TYPE_PASSIVE : WIFI_SCAN_TYPE_ACTIVE;
    cfg.scan_time.active.min = p->active_min;
    cfg.scan_time.active.max = p->active_max;
    cfg.scan_time.passive = p->passive;
    cfg.channel_bitmap.ghz_2_channels = c2 ? c2 : 1;
    cfg.channel_bitmap.ghz_5_channels = c5 ? c5 : 1;
    xSemaphoreTake(done, 0);
    uint32_t t0 = node_ms();
    esp_err_t err = esp_wifi_scan_start(&cfg, false);
    if (err != ESP_OK) {
        uplink_log("scan failed: %s", esp_err_to_name(err));
        vTaskDelay(pdMS_TO_TICKS(1000));
        return;
    }
    uint32_t limit = proto_scan_ms(c2, c5, passive ? p->passive : p->active_max);
    if (!xSemaphoreTake(done, pdMS_TO_TICKS(limit))) {
        uplink_log("scan timed out");
        esp_wifi_scan_stop();
        xSemaphoreTake(done, pdMS_TO_TICKS(1000));
    }
    uint32_t t1 = node_ms();
    results(t0 + (t1 - t0) / 2, gen);
}

static void chunks(const proto_plan_t *p, uint16_t c2, uint32_t c5, bool passive, uint32_t gen)
{
    unsigned n = wire_chunk_size(passive ? p->passive : p->active_max);
    while ((c2 || c5) && node.running && node.role == ROLE_AIR) {
        uint16_t o2;
        uint32_t o5;
        wire_chunk(&c2, &c5, n, &o2, &o5);
        part(p, o2, o5, passive, gen);
        air_handover();
    }
}

static void apply_country(const char *cc)
{
    esp_err_t err = esp_wifi_set_country_code(cc, false);
    if (err != ESP_OK) {
        uplink_log("country %s refused: %s", cc, esp_err_to_name(err));
    }
}

static void scan_task(void *arg)
{
    uint32_t gen = node.gen, plan_gen = node.plan_gen;
    char country[3] = "";
    proto_plan_t p;
    for (;;) {
        if (!node.running || node.role == ROLE_HUB) {
            ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(500));
            continue;
        }
        node_plan(&p);
        if (plan_gen != node.plan_gen || strcmp(country, p.country)) {
            plan_gen = node.plan_gen;
            memcpy(country, p.country, 3);
            apply_country(country);
        }
        if (gen != node.gen) {
            gen = node.gen;
            seen_clear(&seen);
            node.networks = 0;
        }
        uint16_t a2, p2;
        uint32_t a5, p5;
        proto_split(&p, &a2, &a5, &p2, &p5);
        uint32_t start = node_ms();
        bool air = node.role == ROLE_AIR;
        if (air) {
            chunks(&p, a2, a5, false, gen);
            chunks(&p, p2, p5, true, gen);
        } else {
            part(&p, a2, a5, false, gen);
            if (node.running) {
                part(&p, p2, p5, true, gen);
            }
        }
        if (!a2 && !a5 && !p2 && !p5) {
            ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1000));
            if (air) {
                air_handover();
            }
        }
        if (node.running) {
            node.last_sweep = node_ms() - start;
            node.sweeps++;
            if (!air) {
                led_pulse();
            }
        }
    }
}

void scan_init(const char *country)
{
    done = xSemaphoreCreateBinary();
    assert(done);
    ESP_ERROR_CHECK(seen_init(&seen, NODE_WIFI_MAX) == 0 ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_SCAN_DONE, on_event, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_ERROR_CHECK(esp_wifi_set_band_mode(WIFI_BAND_MODE_AUTO));
    apply_country(country);
    xTaskCreate(scan_task, "scan", 4608, NULL, 4, &task);
}

void scan_wake(void)
{
    if (task) {
        xTaskNotifyGive(task);
    }
}

void scan_stop(void)
{
    esp_wifi_scan_stop();
    scan_wake();
}
