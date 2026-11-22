#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "proto.h"

#define NODE_FW "1.0.0"
#define NODE_WIFI_MAX 2048
#define NODE_BLE_MAX 1024
#define NODE_HOST_TIMEOUT_MS 30000

typedef struct {
    volatile bool running, no_ble;
    volatile uint32_t gen;
    volatile uint32_t plan_gen;
    volatile uint32_t sweeps, last_sweep, networks, devices, dropped;
    proto_plan_t plan;
    portMUX_TYPE lock;
} node_t;

extern node_t node;

uint32_t node_ms(void);
void node_plan(proto_plan_t *out);
void node_hello(proto_hello_t *h);
void node_status(proto_status_t *s);
void node_line(const char *line, size_t n);

void uplink_init(size_t tx_buf);
bool uplink_wifi(const proto_wifi_t *w, uint32_t t, TickType_t wait);
bool uplink_ble(const proto_ble_t *b, uint32_t t, TickType_t wait);
void uplink_ack(const char *cmd, bool ok, const char *detail);
void uplink_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void uplink_hello(void);
void uplink_status(void);
void uplink_flush(uint32_t ms);

void scan_init(const char *country);
void scan_wake(void);
void scan_stop(void);

void ble_set(bool on);

void led_init(const proto_led_t *cfg);
void led_config(const proto_led_t *cfg);
void led_ident(unsigned seconds);
void led_pulse(void);
