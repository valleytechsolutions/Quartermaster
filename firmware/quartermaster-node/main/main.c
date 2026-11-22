#include <stdio.h>
#include <string.h>

#include "esp_chip_info.h"
#include "esp_efuse.h"
#include "esp_efuse_table.h"
#include "esp_flash.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "soc/lp_aon_reg.h"
#include "soc/soc.h"
#if CONFIG_SPIRAM
#include "esp_psram.h"
#endif

#include "node.h"

#define STATUS_MS 2000
#define HELLO_IDLE_MS 10000
#define HELLO_RUN_MS 30000

node_t node = {.lock = portMUX_INITIALIZER_UNLOCKED};

static char node_id[13];
static char stored_cc[3] = "01";
static bool have_plan;
static volatile uint32_t last_host;
static unsigned flash_mb, psram_mb;
static const char *board = "c5";

uint32_t node_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

void node_plan(proto_plan_t *out)
{
    portENTER_CRITICAL(&node.lock);
    *out = node.plan;
    portEXIT_CRITICAL(&node.lock);
}

void node_hello(proto_hello_t *h)
{
    esp_chip_info_t chip;
    esp_chip_info(&chip);
    proto_plan_t p;
    node_plan(&p);
    memset(h, 0, sizeof(*h));
    memcpy(h->id, node_id, sizeof(h->id));
    h->fw = NODE_FW;
    h->rev_major = chip.revision / 100;
    h->rev_minor = chip.revision % 100;
    h->flash_mb = flash_mb;
    h->psram_mb = psram_mb;
    h->board = board;
    h->caps = node.no_ble ? "wifi24+wifi5" : "wifi24+wifi5+ble";
    memcpy(h->country, p.country, 3);
    h->plan = p.version;
    h->running = node.running;
}

void node_status(proto_status_t *s)
{
    s->uptime = node_ms();
    s->sweeps = node.sweeps;
    s->last_sweep = node.last_sweep;
    s->networks = node.networks;
    s->devices = node.devices;
    s->dropped = node.dropped;
    s->plan = node.plan.version;
    s->heap = esp_get_free_heap_size();
    s->running = node.running;
}

static nvs_handle_t store(void)
{
    nvs_handle_t h = 0;
    return nvs_open("qm", NVS_READWRITE, &h) == ESP_OK ? h : 0;
}

static void load(proto_led_t *led)
{
    memset(led, 0, sizeof(*led));
    nvs_handle_t h = store();
    if (!h) {
        return;
    }
    size_t n = sizeof(stored_cc);
    char cc[3];
    if (nvs_get_str(h, "cc", cc, &n) == ESP_OK && proto_country_ok(cc)) {
        memcpy(stored_cc, cc, 3);
    }
    uint8_t kind = 0, gpio = 0, al = 0;
    if (nvs_get_u8(h, "led_kind", &kind) == ESP_OK && kind <= LED_WS2812) {
        nvs_get_u8(h, "led_gpio", &gpio);
        nvs_get_u8(h, "led_low", &al);
        led->kind = kind;
        led->gpio = gpio;
        led->active_low = al;
    }
    nvs_close(h);
}

static void save_country(const char *cc)
{
    if (strcmp(cc, stored_cc) == 0) {
        return;
    }
    memcpy(stored_cc, cc, 3);
    nvs_handle_t h = store();
    if (h) {
        nvs_set_str(h, "cc", cc);
        nvs_commit(h);
        nvs_close(h);
    }
}

static bool save_led(const proto_led_t *l)
{
    nvs_handle_t h = store();
    if (!h) {
        return false;
    }
    bool ok = nvs_set_u8(h, "led_kind", l->kind) == ESP_OK && nvs_set_u8(h, "led_gpio", l->gpio) == ESP_OK &&
              nvs_set_u8(h, "led_low", l->active_low) == ESP_OK && nvs_commit(h) == ESP_OK;
    nvs_close(h);
    return ok;
}

static void set_plan(const proto_plan_t *p)
{
    portENTER_CRITICAL(&node.lock);
    node.plan = *p;
    node.plan_gen++;
    node.gen++;
    node.networks = node.devices = 0;
    portEXIT_CRITICAL(&node.lock);
}

static void start(void)
{
    if (!have_plan) {
        proto_plan_t p;
        proto_default_plan(&p, stored_cc);
        set_plan(&p);
    } else {
        node.gen++;
        node.networks = node.devices = 0;
    }
    node.sweeps = 0;
    node.last_sweep = 0;
    node.running = true;
    ble_set(node.plan.ble);
    scan_wake();
}

static void stop(void)
{
    node.running = false;
    ble_set(false);
    scan_stop();
}

static void restart(bool download)
{
    stop();
    uplink_flush(300);
    if (download) {
        REG_SET_FIELD(LP_AON_SYS_CFG_REG, LP_AON_FORCE_DOWNLOAD_BOOT, 1);
    }
    esp_restart();
}

void node_line(const char *line, size_t n)
{
    proto_cmd_t c;
    char detail[16] = "";
    const char *err = NULL;
    proto_parse_t r = proto_parse(line, n, &c);
    if (r == PROTO_IGNORE) {
        return;
    }
    last_host = node_ms();
    if (r == PROTO_BADSUM) {
        uplink_ack(c.name, false, "checksum");
        return;
    }
    if (strcmp(c.name, "HELLO") == 0) {
        uplink_ack(c.name, true, "");
        uplink_hello();
        return;
    } else if (strcmp(c.name, "PLAN") == 0) {
        proto_plan_t p;
        if (!(err = proto_plan(&c, &p))) {
            set_plan(&p);
            have_plan = true;
            save_country(p.country);
            if (node.running) {
                ble_set(p.ble);
            }
            snprintf(detail, sizeof(detail), "%lu", (unsigned long)p.version);
        }
    } else if (strcmp(c.name, "START") == 0) {
        start();
    } else if (strcmp(c.name, "STOP") == 0) {
        stop();
    } else if (strcmp(c.name, "IDENT") == 0) {
        unsigned s;
        if (!(err = proto_ident(&c, &s))) {
            led_ident(s);
            snprintf(detail, sizeof(detail), "%u", s);
        }
    } else if (strcmp(c.name, "LED") == 0) {
        proto_led_t l;
        if (!(err = proto_led(&c, &l))) {
            if (save_led(&l)) {
                led_config(&l);
            } else {
                err = "storage";
            }
        }
    } else if (strcmp(c.name, "BOOTLOADER") == 0 || strcmp(c.name, "REBOOT") == 0) {
        uplink_ack(c.name, true, "");
        restart(c.name[0] == 'B');
        return;
    } else {
        err = "unknown";
    }
    uplink_ack(c.name, !err, err ? err : detail);
}

static void identify(void)
{
    uint8_t mac[6] = {0};
    esp_efuse_mac_get_default(mac);
    snprintf(node_id, sizeof(node_id), "%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    uint32_t size = 0;
    if (esp_flash_get_physical_size(NULL, &size) == ESP_OK) {
        flash_mb = size >> 20;
    }
#if CONFIG_SPIRAM
    if (esp_psram_is_initialized()) {
        psram_mb = esp_psram_get_size() >> 20;
    }
#endif
    uint8_t in_package = 0;
    esp_efuse_read_field_blob(ESP_EFUSE_FLASH_CAP, &in_package, 3);
    if (flash_mb >= 8) {
        board = "c5-8mb";
    } else if (flash_mb == 4 && psram_mb == 0 && in_package) {
        board = "stamp-c5";
    }
}

static const char *crash_name(esp_reset_reason_t r)
{
    switch (r) {
    case ESP_RST_PANIC:
        return "panic";
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT:
        return "watchdog";
    case ESP_RST_BROWNOUT:
        return "brownout";
    case ESP_RST_CPU_LOCKUP:
        return "lockup";
    default:
        return NULL;
    }
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    identify();
    proto_led_t led;
    load(&led);
    proto_plan_t p;
    proto_default_plan(&p, stored_cc);
    node.plan = p;
    uplink_init(psram_mb ? 8192 : 4096);
    const char *crash = crash_name(esp_reset_reason());
    if (crash) {
        uplink_log("reset: %s", crash);
    }
    led_init(&led);
    scan_init(stored_cc);
    uplink_hello();

    uint32_t last_status = 0, last_hello = node_ms();
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(100));
        uint32_t host = last_host;
        uint32_t now = node_ms();
        if (node.running && now - host >= NODE_HOST_TIMEOUT_MS) {
            stop();
            uplink_log("no host for %u s, stopped", NODE_HOST_TIMEOUT_MS / 1000);
        }
        if (node.running && now - last_status >= STATUS_MS) {
            last_status = now;
            uplink_status();
        }
        if (now - last_hello >= (node.running ? HELLO_RUN_MS : HELLO_IDLE_MS)) {
            last_hello = now;
            uplink_hello();
        }
    }
}
