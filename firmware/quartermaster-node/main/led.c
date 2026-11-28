#include <string.h>

#include "driver/gpio.h"
#include "driver/rmt_tx.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "chip.h"
#include "node.h"

#define TICK_MS 20
#define PULSE_MS 40
#define BLINK_MS 250
#define FAST_MS 125
#define SLOW_MS 2000
#define SLOW_ON_MS 120
#define RMT_HZ 10000000

enum { OFF, PULSE, IDENT };

static const uint8_t colours[][3] = {{0, 0, 0}, {24, 40, 0}, {48, 48, 48}};

static portMUX_TYPE lock = portMUX_INITIALIZER_UNLOCKED;
static proto_led_t want, cur;
static volatile bool changed;
static volatile uint32_t ident_until, pulse_until;
static volatile int blink;
static rmt_channel_handle_t chan;
static rmt_encoder_handle_t enc;

static void show(int level)
{
    if (cur.kind == LED_GPIO) {
        gpio_set_level(cur.gpio, (level != OFF) ^ cur.active_low);
    } else if (cur.kind == LED_WS2812 && chan) {
        const uint8_t *c = colours[level];
        uint8_t grb[3] = {c[1], c[0], c[2]};
        rmt_transmit_config_t tx = {.loop_count = 0};
        if (rmt_transmit(chan, enc, grb, sizeof(grb), &tx) == ESP_OK) {
            rmt_tx_wait_all_done(chan, 10);
        }
    }
}

static void release(void)
{
    if (cur.kind == LED_WS2812 && chan) {
        rmt_disable(chan);
        rmt_del_channel(chan);
        rmt_del_encoder(enc);
        chan = NULL;
        enc = NULL;
    }
    if (cur.kind != LED_NONE && cur.gpio == CHIP_BUTTON) {
        gpio_config_t io = {.pin_bit_mask = 1ULL << CHIP_BUTTON, .mode = GPIO_MODE_INPUT, .pull_up_en = 1};
        gpio_config(&io);
    } else if (cur.kind != LED_NONE) {
        gpio_reset_pin(cur.gpio);
    }
    cur.kind = LED_NONE;
}

static void setup(const proto_led_t *l)
{
    release();
    cur = *l;
    if (cur.kind == LED_GPIO) {
        gpio_config_t io = {.pin_bit_mask = 1ULL << cur.gpio, .mode = GPIO_MODE_OUTPUT};
        if (gpio_config(&io) != ESP_OK) {
            cur.kind = LED_NONE;
        }
    } else if (cur.kind == LED_WS2812) {
        rmt_tx_channel_config_t cc = {
            .gpio_num = cur.gpio,
            .clk_src = RMT_CLK_SRC_DEFAULT,
            .resolution_hz = RMT_HZ,
            .mem_block_symbols = 48,
            .trans_queue_depth = 2,
        };
        rmt_bytes_encoder_config_t bc = {
            .bit0 = {.level0 = 1, .duration0 = 3, .level1 = 0, .duration1 = 9},
            .bit1 = {.level0 = 1, .duration0 = 9, .level1 = 0, .duration1 = 3},
            .flags.msb_first = 1,
        };
        if (rmt_new_tx_channel(&cc, &chan) != ESP_OK) {
            chan = NULL;
            cur.kind = LED_NONE;
        } else if (rmt_new_bytes_encoder(&bc, &enc) != ESP_OK || rmt_enable(chan) != ESP_OK) {
            release();
        }
    }
    show(OFF);
}

static void led_task(void *arg)
{
    int shown = -1;
    for (;;) {
        if (changed) {
            proto_led_t l;
            portENTER_CRITICAL(&lock);
            l = want;
            changed = false;
            portEXIT_CRITICAL(&lock);
            setup(&l);
            shown = OFF;
        }
        uint32_t now = node_ms();
        int level = OFF;
        if ((int32_t)(ident_until - now) > 0) {
            level = (now / BLINK_MS) & 1 ? OFF : IDENT;
        } else if ((int32_t)(pulse_until - now) > 0) {
            level = PULSE;
        } else if (blink == BLINK_FAST) {
            level = (now / FAST_MS) & 1 ? OFF : PULSE;
        } else if (blink == BLINK_SLOW) {
            level = now % SLOW_MS < SLOW_ON_MS ? PULSE : OFF;
        }
        if (level != shown) {
            show(level);
            shown = level;
        }
        vTaskDelay(pdMS_TO_TICKS(TICK_MS));
    }
}

void led_config(const proto_led_t *l)
{
    portENTER_CRITICAL(&lock);
    want = *l;
    changed = true;
    portEXIT_CRITICAL(&lock);
}

void led_init(const proto_led_t *l)
{
    led_config(l);
    xTaskCreate(led_task, "led", 2560, NULL, 3, NULL);
}

void led_ident(unsigned seconds)
{
    ident_until = node_ms() + seconds * 1000;
}

void led_pulse(void)
{
    pulse_until = node_ms() + PULSE_MS;
}

void led_blink(int mode)
{
    blink = mode;
}
