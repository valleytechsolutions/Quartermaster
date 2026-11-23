#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "driver/uart.h"
#include "driver/usb_serial_jtag.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/ringbuf.h"
#include "freertos/task.h"

#include "node.h"

#define QUEUE_LEN 128
#define RX_BUF 512
#define UART_PORT UART_NUM_0
#define UART_TX 11
#define UART_RX 12
#define BAUD 921600
#define HOST_RETRY_MS 500
#define DETAIL_MAX 32
#define LOG_MAX 64
#define RAW_POLL_MS 10

enum { PORT_USB, PORT_UART, PORTS, PORT_UNKNOWN = -1 };

typedef struct {
    char kind;
    uint32_t t;
    union {
        proto_wifi_t w;
        proto_ble_t b;
        struct {
            char cmd[17];
            char detail[DETAIL_MAX + 1];
            bool ok;
        } a;
        char text[LOG_MAX + 1];
        wire_cluster_t c;
        wire_node_t n;
        wire_joiner_t j;
    };
} rec_t;

static QueueHandle_t queue;
static RingbufHandle_t raw;
static uint32_t seq;
static volatile int host_port = PORT_UNKNOWN;

static int port_write(int port, const char *s, size_t n)
{
    if (port == PORT_USB) {
        if (!usb_serial_jtag_is_connected()) {
            return -1;
        }
        return usb_serial_jtag_write_bytes(s, n, 0) == (int)n;
    }
    size_t room = 0;
    if (uart_get_tx_buffer_free_size(UART_PORT, &room) != ESP_OK || room < n) {
        return 0;
    }
    return uart_write_bytes(UART_PORT, s, n) == (int)n;
}

static void emit(const char *s, int n)
{
    if (n <= 0) {
        return;
    }
    for (int port = 0; port < PORTS; port++) {
        uint32_t start = node_ms();
        while (port_write(port, s, n) == 0 && port == host_port && node_ms() - start < HOST_RETRY_MS) {
            vTaskDelay(1);
        }
    }
}

static void write_rec(const rec_t *r)
{
    char line[PROTO_LINE_MAX + 1];
    int n = -1;
    switch (r->kind) {
    case 'W':
        n = proto_wifi(line, sizeof(line), seq++, node_ms() - r->t, &r->w);
        break;
    case 'B':
        n = proto_ble(line, sizeof(line), seq++, node_ms() - r->t, &r->b);
        break;
    case 'A':
        n = proto_ack(line, sizeof(line), seq++, r->a.cmd, r->a.ok, r->a.detail);
        break;
    case 'L':
        n = proto_log(line, sizeof(line), seq++, r->text);
        break;
    case 'S': {
        proto_status_t s;
        node_status(&s);
        n = proto_status(line, sizeof(line), seq++, &s);
        break;
    }
    case 'H': {
        proto_hello_t h;
        node_hello(&h);
        n = proto_hello(line, sizeof(line), &h);
        break;
    }
    case 'C':
        n = wire_cluster_line(line, sizeof(line), seq++, &r->c);
        break;
    case 'N':
        n = wire_node_line(line, sizeof(line), seq++, &r->n);
        break;
    case 'J':
        n = wire_join_line(line, sizeof(line), seq++, &r->j);
        break;
    }
    if (node.role == ROLE_AIR) {
        if (n > 0) {
            air_line(line, n);
        }
    } else {
        emit(line, n);
    }
}

static void drain_raw(void)
{
    size_t n;
    char *s;
    while (raw && (s = xRingbufferReceive(raw, &n, 0))) {
        emit(s, n);
        vRingbufferReturnItem(raw, s);
    }
}

static void tx_task(void *arg)
{
    rec_t r;
    for (;;) {
        bool hub = node.role == ROLE_HUB;
        if (xQueueReceive(queue, &r, hub ? pdMS_TO_TICKS(RAW_POLL_MS) : portMAX_DELAY)) {
            write_rec(&r);
        }
        if (hub) {
            drain_raw();
        }
    }
}

typedef struct {
    char buf[PROTO_LINE_MAX + 1];
    size_t len;
    bool skip;
} rx_line_t;

static void rx_bytes(int port, rx_line_t *l, const uint8_t *b, int n)
{
    for (int i = 0; i < n; i++) {
        char c = b[i];
        if (c == '\n' || c == '\r') {
            if (!l->skip && l->len) {
                if (l->buf[0] == '#') {
                    host_port = port;
                }
                node_line(l->buf, l->len);
            }
            l->len = 0;
            l->skip = false;
        } else if (l->len < PROTO_LINE_MAX) {
            l->buf[l->len++] = c;
        } else {
            l->skip = true;
        }
    }
}

static void rx_task(void *arg)
{
    static rx_line_t lines[PORTS];
    uint8_t b[64];
    for (;;) {
        int got = 0, n;
        n = usb_serial_jtag_read_bytes(b, sizeof(b), 0);
        if (n > 0) {
            rx_bytes(PORT_USB, &lines[PORT_USB], b, n);
            got += n;
        }
        n = uart_read_bytes(UART_PORT, b, sizeof(b), 0);
        if (n > 0) {
            rx_bytes(PORT_UART, &lines[PORT_UART], b, n);
            got += n;
        }
        if (!got) {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }
}

void uplink_init(size_t tx_buf)
{
    usb_serial_jtag_driver_config_t usb = {.tx_buffer_size = tx_buf, .rx_buffer_size = RX_BUF};
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&usb));
    uart_config_t cfg = {
        .baud_rate = BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_driver_install(UART_PORT, RX_BUF, tx_buf, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(UART_PORT, &cfg));
    ESP_ERROR_CHECK(uart_set_pin(UART_PORT, UART_TX, UART_RX, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    queue = xQueueCreate(QUEUE_LEN, sizeof(rec_t));
    raw = xRingbufferCreate(tx_buf, RINGBUF_TYPE_NOSPLIT);
    assert(queue && raw);
    xTaskCreate(tx_task, "uplink", 4096, NULL, 5, NULL);
    xTaskCreate(rx_task, "cmd", 6144, NULL, 6, NULL);
}

static bool put(const rec_t *r, TickType_t wait)
{
    if (xQueueSend(queue, r, wait) == pdTRUE) {
        return true;
    }
    __atomic_add_fetch(&node.dropped, 1, __ATOMIC_RELAXED);
    return false;
}

bool uplink_wifi(const proto_wifi_t *w, uint32_t t, TickType_t wait)
{
    rec_t r = {.kind = 'W', .t = t, .w = *w};
    return put(&r, wait);
}

bool uplink_ble(const proto_ble_t *b, uint32_t t, TickType_t wait)
{
    rec_t r = {.kind = 'B', .t = t, .b = *b};
    return put(&r, wait);
}

void uplink_ack(const char *cmd, bool ok, const char *detail)
{
    rec_t r = {.kind = 'A'};
    snprintf(r.a.cmd, sizeof(r.a.cmd), "%s", cmd);
    snprintf(r.a.detail, sizeof(r.a.detail), "%s", detail ? detail : "");
    r.a.ok = ok;
    put(&r, pdMS_TO_TICKS(100));
}

void uplink_log(const char *fmt, ...)
{
    rec_t r = {.kind = 'L'};
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(r.text, sizeof(r.text), fmt, ap);
    va_end(ap);
    put(&r, pdMS_TO_TICKS(100));
}

void uplink_hello(void)
{
    rec_t r = {.kind = 'H'};
    put(&r, pdMS_TO_TICKS(100));
}

void uplink_status(void)
{
    rec_t r = {.kind = 'S'};
    put(&r, pdMS_TO_TICKS(100));
}

void uplink_flush(uint32_t ms)
{
    uint32_t start = node_ms();
    while (uxQueueMessagesWaiting(queue) && node_ms() - start < ms) {
        vTaskDelay(pdMS_TO_TICKS(5));
    }
    uart_wait_tx_done(UART_PORT, pdMS_TO_TICKS(50));
    usb_serial_jtag_wait_tx_done(pdMS_TO_TICKS(50));
    vTaskDelay(pdMS_TO_TICKS(20));
}

void uplink_raw(const char *line, size_t n)
{
    if (xRingbufferSend(raw, line, n, 0) != pdTRUE) {
        __atomic_add_fetch(&node.dropped, 1, __ATOMIC_RELAXED);
    }
}

void uplink_cluster(const wire_cluster_t *c)
{
    rec_t r = {.kind = 'C', .c = *c};
    put(&r, pdMS_TO_TICKS(100));
}

void uplink_node(const wire_node_t *n)
{
    rec_t r = {.kind = 'N', .n = *n};
    put(&r, pdMS_TO_TICKS(100));
}

void uplink_join(const wire_joiner_t *j)
{
    rec_t r = {.kind = 'J', .j = *j};
    put(&r, pdMS_TO_TICKS(100));
}
