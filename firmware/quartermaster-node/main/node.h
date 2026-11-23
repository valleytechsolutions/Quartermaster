#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "proto.h"
#include "wire.h"

#define NODE_FW "1.1.0"
#define NODE_WIFI_MAX 2048
#define NODE_BLE_MAX 1024
#define NODE_HOST_TIMEOUT_MS 30000
#define NODE_AIR_AFTER_MS 10000

enum { ROLE_USB, ROLE_HUB, ROLE_AIR };

enum { BLINK_OFF, BLINK_SLOW, BLINK_FAST };

typedef struct {
    volatile bool running, no_ble;
    volatile uint8_t role;
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
void node_remote(const char *line, size_t n);
void node_run(bool on);
bool node_save(const char *key, const void *d, size_t n);
size_t node_load(const char *key, void *d, size_t cap);
void node_erase(const char *key);

void uplink_init(size_t tx_buf);
bool uplink_wifi(const proto_wifi_t *w, uint32_t t, TickType_t wait);
bool uplink_ble(const proto_ble_t *b, uint32_t t, TickType_t wait);
void uplink_ack(const char *cmd, bool ok, const char *detail);
void uplink_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void uplink_hello(void);
void uplink_status(void);
void uplink_flush(uint32_t ms);
void uplink_raw(const char *line, size_t n);
void uplink_cluster(const wire_cluster_t *c);
void uplink_node(const wire_node_t *n);
void uplink_join(const wire_joiner_t *j);

void scan_init(const char *country);
void scan_wake(void);
void scan_stop(void);

void ble_set(bool on);

void led_init(const proto_led_t *cfg);
void led_config(const proto_led_t *cfg);
void led_ident(unsigned seconds);
void led_pulse(void);
void led_blink(int mode);

typedef struct {
    uint8_t mac[6];
    int8_t rssi;
    uint16_t len;
    uint8_t data[];
} radio_rx_t;

bool radio_start(void);
void radio_stop(void);
size_t radio_max(void);
bool radio_send(const uint8_t *mac, const void *d, size_t n);
radio_rx_t *radio_recv(uint32_t ms);
void radio_done(radio_rx_t *r);
void radio_drain(void);
bool radio_channel(uint8_t ch);
void radio_mac(uint8_t mac[6]);
void radio_pmk(const uint8_t key[WIRE_KEY]);
bool radio_peer(const uint8_t mac[6], const uint8_t key[WIRE_KEY], const uint8_t node[6]);
void radio_unpeer(const uint8_t mac[6]);
bool radio_kp_new(uint8_t pub[WIRE_PUB]);
void radio_kp_free(void);
bool radio_kek(const uint8_t peer[WIRE_PUB], const uint8_t node[6], const uint8_t hub[6], uint8_t kek[32]);
void radio_wrap(const uint8_t kek[32], const uint8_t key[WIRE_KEY], uint8_t wrapped[WIRE_KEY],
                uint8_t check[WIRE_CHECK]);
bool radio_unwrap(const uint8_t kek[32], const uint8_t wrapped[WIRE_KEY], const uint8_t check[WIRE_CHECK],
                  uint8_t key[WIRE_KEY]);

void hub_init(void);
const char *hub_begin(uint8_t home, const uint8_t key[WIRE_KEY]);
void hub_end(void);
const char *hub_pair(unsigned seconds);
const char *hub_admit(const uint8_t mac[6]);
const char *hub_forget(const uint8_t mac[6]);
const char *hub_to(const uint8_t mac[6], const char *line);
void hub_report(void);

void air_init(size_t outbox);
void air_begin(void);
void air_end(void);
bool air_line(const char *line, size_t n);
void air_handover(void);
bool air_linked(void);
void air_unpair(void);
void air_reboot(void);
