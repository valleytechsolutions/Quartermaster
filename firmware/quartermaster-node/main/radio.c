#include <string.h>

#include "esp_heap_caps.h"
#include "esp_now.h"
#include "esp_random.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/ringbuf.h"
#include "freertos/semphr.h"
#include "mbedtls/ecdh.h"
#include "mbedtls/ecp.h"
#include "mbedtls/md.h"
#include "mbedtls/sha256.h"

#include "node.h"

#define RX_RING 8192
#define SEND_WAIT_MS 60
#define SHORT_MAX 250

static const uint8_t broadcast[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};

static RingbufHandle_t rx;
static SemaphoreHandle_t sent;
static portMUX_TYPE tx_lock = portMUX_INITIALIZER_UNLOCKED;
static wire_tx_t tx;
static volatile bool listening;
static size_t frame_max = WIRE_FRAME_MAX;
static bool started;
static mbedtls_ecp_group grp;
static mbedtls_mpi priv;
static mbedtls_ecp_point pub;
static bool have_kp;

static void on_recv(const esp_now_recv_info_t *info, const uint8_t *d, int n)
{
    if (!listening || n <= 0 || n > WIRE_FRAME_MAX) {
        return;
    }
    radio_rx_t *r = NULL;
    if (xRingbufferSendAcquire(rx, (void **)&r, sizeof(*r) + n, 0) != pdTRUE) {
        return;
    }
    memcpy(r->mac, info->src_addr, 6);
    r->rssi = info->rx_ctrl ? info->rx_ctrl->rssi : 0;
    r->len = n;
    memcpy(r->data, d, n);
    xRingbufferSendComplete(rx, r);
}

static void on_sent(const esp_now_send_info_t *info, esp_now_send_status_t status)
{
    portENTER_CRITICAL(&tx_lock);
    wire_tx_done(&tx, status == ESP_NOW_SEND_SUCCESS);
    portEXIT_CRITICAL(&tx_lock);
    xSemaphoreGive(sent);
}

bool radio_start(void)
{
    if (!started) {
        rx = xRingbufferCreateWithCaps(RX_RING, RINGBUF_TYPE_NOSPLIT,
                                       heap_caps_get_total_size(MALLOC_CAP_SPIRAM) ? MALLOC_CAP_SPIRAM
                                                                                   : MALLOC_CAP_INTERNAL);
        sent = xSemaphoreCreateBinary();
        if (!rx || !sent || esp_now_init() != ESP_OK) {
            uplink_log("wireless: could not start");
            return false;
        }
        esp_now_register_recv_cb(on_recv);
        esp_now_register_send_cb(on_sent);
        esp_now_peer_info_t p = {.ifidx = WIFI_IF_STA};
        memcpy(p.peer_addr, broadcast, 6);
        esp_now_add_peer(&p);
        started = true;
    }
    radio_drain();
    listening = true;
    return true;
}

void radio_stop(void)
{
    listening = false;
    if (started) {
        radio_drain();
    }
}

size_t radio_max(void)
{
    return frame_max;
}

static int tx_wait(uint32_t start)
{
    for (;;) {
        uint32_t now = node_ms(), spent = now - start;
        portENTER_CRITICAL(&tx_lock);
        int r = wire_tx_result(&tx);
        portEXIT_CRITICAL(&tx_lock);
        if (r >= 0 || spent >= SEND_WAIT_MS) {
            return r;
        }
        xSemaphoreTake(sent, pdMS_TO_TICKS(SEND_WAIT_MS - spent));
    }
}

bool radio_send(const uint8_t *mac, const void *d, size_t n)
{
    if (!started) {
        return false;
    }
    uint32_t now = node_ms();
    portENTER_CRITICAL(&tx_lock);
    bool ready = wire_tx_ready(&tx, now);
    portEXIT_CRITICAL(&tx_lock);
    if (!ready && tx_wait(now) < 0) {
        return false;
    }
    now = node_ms();
    portENTER_CRITICAL(&tx_lock);
    wire_tx_ready(&tx, now);
    wire_tx_issued(&tx, now);
    portEXIT_CRITICAL(&tx_lock);
    esp_err_t err = esp_now_send(mac ? mac : broadcast, d, n);
    if (err == ESP_ERR_ESPNOW_ARG && n > SHORT_MAX && frame_max > SHORT_MAX) {
        frame_max = SHORT_MAX;
        uplink_log("wireless: short frames only");
    }
    if (err != ESP_OK) {
        portENTER_CRITICAL(&tx_lock);
        tx.issued--;
        portEXIT_CRITICAL(&tx_lock);
        return false;
    }
    return tx_wait(now) == 1;
}

radio_rx_t *radio_recv(uint32_t ms)
{
    size_t n = 0;
    return started ? xRingbufferReceive(rx, &n, pdMS_TO_TICKS(ms)) : NULL;
}

void radio_done(radio_rx_t *r)
{
    vRingbufferReturnItem(rx, r);
}

void radio_drain(void)
{
    radio_rx_t *r;
    while ((r = radio_recv(0))) {
        radio_done(r);
    }
}

bool radio_channel(uint8_t ch)
{
    uint8_t cur = 0;
    wifi_second_chan_t sec;
    if (esp_wifi_get_channel(&cur, &sec) == ESP_OK && cur == ch) {
        return true;
    }
    return esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE) == ESP_OK;
}

void radio_mac(uint8_t mac[6])
{
    esp_wifi_get_mac(WIFI_IF_STA, mac);
}

static void hmac(const uint8_t key[WIRE_KEY], const char *label, const uint8_t *extra, size_t n, uint8_t out[32])
{
    uint8_t in[16 + 6];
    size_t l = strlen(label);
    memcpy(in, label, l);
    if (n) {
        memcpy(in + l, extra, n);
    }
    mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), key, WIRE_KEY, in, l + n, out);
}

void radio_pmk(const uint8_t key[WIRE_KEY])
{
    uint8_t k[32];
    hmac(key, "qm-pmk", NULL, 0, k);
    esp_now_set_pmk(k);
}

bool radio_peer(const uint8_t mac[6], const uint8_t key[WIRE_KEY], const uint8_t node[6])
{
    esp_now_peer_info_t p = {.ifidx = WIFI_IF_STA, .encrypt = true};
    uint8_t k[32];
    hmac(key, "qm-lmk", node, 6, k);
    memcpy(p.peer_addr, mac, 6);
    memcpy(p.lmk, k, ESP_NOW_KEY_LEN);
    esp_err_t err = esp_now_is_peer_exist(mac) ? esp_now_mod_peer(&p) : esp_now_add_peer(&p);
    if (err != ESP_OK) {
        uplink_log("wireless: peer refused: %s", esp_err_to_name(err));
    }
    return err == ESP_OK;
}

void radio_unpeer(const uint8_t mac[6])
{
    if (started && esp_now_is_peer_exist(mac)) {
        esp_now_del_peer(mac);
    }
}

static int rng(void *ctx, unsigned char *b, size_t n)
{
    esp_fill_random(b, n);
    return 0;
}

void radio_kp_free(void)
{
    if (have_kp) {
        mbedtls_ecp_point_free(&pub);
        mbedtls_mpi_free(&priv);
        mbedtls_ecp_group_free(&grp);
        have_kp = false;
    }
}

bool radio_kp_new(uint8_t out[WIRE_PUB])
{
    radio_kp_free();
    mbedtls_ecp_group_init(&grp);
    mbedtls_mpi_init(&priv);
    mbedtls_ecp_point_init(&pub);
    have_kp = true;
    size_t n = 0;
    if (mbedtls_ecp_group_load(&grp, MBEDTLS_ECP_DP_SECP256R1) ||
        mbedtls_ecp_gen_keypair(&grp, &priv, &pub, rng, NULL) ||
        mbedtls_ecp_point_write_binary(&grp, &pub, MBEDTLS_ECP_PF_UNCOMPRESSED, &n, out, WIRE_PUB) || n != WIRE_PUB) {
        radio_kp_free();
        return false;
    }
    return true;
}

bool radio_kek(const uint8_t peer[WIRE_PUB], const uint8_t node[6], const uint8_t hub[6], uint8_t kek[32])
{
    if (!have_kp) {
        return false;
    }
    mbedtls_ecp_point q;
    mbedtls_mpi z;
    mbedtls_ecp_point_init(&q);
    mbedtls_mpi_init(&z);
    uint8_t secret[32];
    bool ok = !mbedtls_ecp_point_read_binary(&grp, &q, peer, WIRE_PUB) && !mbedtls_ecp_check_pubkey(&grp, &q) &&
              !mbedtls_ecdh_compute_shared(&grp, &z, &q, &priv, rng, NULL) &&
              !mbedtls_mpi_write_binary(&z, secret, sizeof(secret));
    mbedtls_ecp_point_free(&q);
    mbedtls_mpi_free(&z);
    if (ok) {
        mbedtls_sha256_context s;
        mbedtls_sha256_init(&s);
        ok = !mbedtls_sha256_starts(&s, 0) && !mbedtls_sha256_update(&s, (const uint8_t *)"qm-join", 7) &&
             !mbedtls_sha256_update(&s, secret, sizeof(secret)) && !mbedtls_sha256_update(&s, node, 6) &&
             !mbedtls_sha256_update(&s, hub, 6) && !mbedtls_sha256_finish(&s, kek);
        mbedtls_sha256_free(&s);
    }
    memset(secret, 0, sizeof(secret));
    return ok;
}

static void check_of(const uint8_t kek[32], const uint8_t key[WIRE_KEY], uint8_t out[WIRE_CHECK])
{
    uint8_t h[32];
    mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), kek + 16, 16, key, WIRE_KEY, h);
    memcpy(out, h, WIRE_CHECK);
}

void radio_wrap(const uint8_t kek[32], const uint8_t key[WIRE_KEY], uint8_t wrapped[WIRE_KEY],
                uint8_t check[WIRE_CHECK])
{
    for (int i = 0; i < WIRE_KEY; i++) {
        wrapped[i] = key[i] ^ kek[i];
    }
    check_of(kek, key, check);
}

bool radio_unwrap(const uint8_t kek[32], const uint8_t wrapped[WIRE_KEY], const uint8_t check[WIRE_CHECK],
                  uint8_t key[WIRE_KEY])
{
    uint8_t c[WIRE_CHECK];
    for (int i = 0; i < WIRE_KEY; i++) {
        key[i] = wrapped[i] ^ kek[i];
    }
    check_of(kek, key, c);
    uint8_t diff = 0;
    for (int i = 0; i < WIRE_CHECK; i++) {
        diff |= c[i] ^ check[i];
    }
    return diff == 0;
}
