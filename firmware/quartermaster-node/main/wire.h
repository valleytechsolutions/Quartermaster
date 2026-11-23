#pragma once

#include <stddef.h>
#include <stdint.h>

#include "proto.h"

#define WIRE_MAGIC 'Q'
#define WIRE_VERSION 1
#define WIRE_NODES 8
#define WIRE_SLOTS 10
#define WIRE_KEY 16
#define WIRE_PUB 65
#define WIRE_CHECK 8
#define WIRE_NAME 8
#define WIRE_FRAME_MAX 1400
#define WIRE_LINE_MAX 210
#define WIRE_CMDS 4
#define WIRE_CMD_MAX 96
#define WIRE_TRIES 6
#define WIRE_HOME_MAX 11
#define WIRE_HOME_DEFAULT 6
#define WIRE_PAIR_DEFAULT 60
#define WIRE_PAIR_MAX 600
#define WIRE_SILENCE_MS 60000u
#define WIRE_JOIN_KEEP_MS 30000u
#define WIRE_JOIN_TELL_MS 5000u
#define WIRE_WAIT_MS 10000u
#define WIRE_SEARCH_HOME 3
#define WIRE_DATA_HEAD 18
#define WIRE_REC_HEAD 5
#define WIRE_TX_STALE_MS 1000u
#define WIRE_HOLD_MS 2000u

enum { WIRE_JOIN = 1, WIRE_WAIT, WIRE_ADMIT, WIRE_DATA, WIRE_ACK, WIRE_BYE };

enum { WD_CMD_ACK = 1, WD_POLL = 2, WD_RUN = 4 };

enum { WA_RUN = 1, WA_BYE = 2, WA_POLL = 4 };

enum { PEER_FREE, PEER_NEW, PEER_UP, PEER_LEAVING };

typedef struct {
    char fw[WIRE_NAME + 1];
    char board[WIRE_NAME + 1];
    uint8_t pub[WIRE_PUB];
} wire_join_t;

typedef struct {
    uint8_t to[6];
    uint8_t channel;
    uint16_t seconds;
} wire_wait_t;

typedef struct {
    uint8_t to[6];
    uint8_t channel;
    uint8_t pub[WIRE_PUB];
    uint8_t wrapped[WIRE_KEY];
    uint8_t check[WIRE_CHECK];
} wire_admit_t;

typedef struct {
    uint32_t session, seq, now;
    uint8_t cmd_ack, flags, count;
    const uint8_t *recs;
    size_t recs_len;
} wire_data_t;

typedef struct {
    uint8_t id, len;
    const char *line;
} wire_cmd_t;

typedef struct {
    uint32_t session, seq;
    uint8_t flags, ncmd;
    wire_cmd_t cmd[WIRE_CMDS];
} wire_ack_t;

typedef struct {
    uint8_t *buf;
    size_t cap, len, sent;
    uint8_t sent_n, tries;
    uint32_t seq, lost;
} wire_out_t;

typedef struct {
    uint8_t have, last;
} wire_seen_t;

typedef struct {
    uint8_t id, len;
    char line[WIRE_CMD_MAX];
} wire_queued_t;

typedef struct {
    uint8_t mac[6];
    uint8_t state, have_seq, heard_ok, synced, next_id, ncmd;
    int8_t rssi;
    uint32_t session, seq, since, heard, frames, resent;
    wire_queued_t cmd[WIRE_CMDS];
} wire_peer_t;

typedef struct {
    uint8_t used;
    uint8_t mac[6];
    int8_t rssi;
    uint32_t heard, told;
    wire_join_t join;
} wire_cand_t;

typedef struct {
    uint8_t open;
    uint32_t until;
    wire_cand_t cand[WIRE_NODES];
} wire_pair_t;

typedef enum { AIR_HOP, AIR_WAIT, AIR_SEARCH, AIR_LINKED } wire_air_state_t;

typedef struct {
    wire_air_state_t state;
    uint8_t home, ch, tries;
    int8_t run;
    uint32_t heard;
} wire_air_t;

typedef struct {
    uint32_t issued, done, at;
    uint8_t ok;
} wire_tx_t;

typedef struct {
    uint8_t armed, held;
    uint32_t down;
} wire_button_t;

typedef struct {
    uint8_t mac[6];
    uint8_t state, heard_ok;
    int8_t rssi;
    uint32_t ago, frames, resent;
} wire_node_t;

typedef struct {
    uint8_t home, nodes, running;
    uint16_t left;
    uint32_t relayed, dropped;
} wire_cluster_t;

typedef struct {
    uint8_t mac[6];
    int8_t rssi;
    char fw[WIRE_NAME + 1];
    char board[WIRE_NAME + 1];
} wire_joiner_t;

void wire_hexid(const uint8_t mac[6], char out[13]);
int wire_id(const char *s, uint8_t mac[6]);
int wire_type(const uint8_t *d, size_t n);

int wire_join_put(uint8_t *out, size_t cap, const wire_join_t *j);
int wire_join_get(const uint8_t *d, size_t n, wire_join_t *j);
int wire_wait_put(uint8_t *out, size_t cap, const wire_wait_t *w);
int wire_wait_get(const uint8_t *d, size_t n, wire_wait_t *w);
int wire_admit_put(uint8_t *out, size_t cap, const wire_admit_t *a);
int wire_admit_get(const uint8_t *d, size_t n, wire_admit_t *a);
int wire_bye_put(uint8_t *out, size_t cap);
int wire_data_get(const uint8_t *d, size_t n, wire_data_t *w);
int wire_rec_next(const wire_data_t *w, size_t *pos, uint32_t *stamp, const char **line, size_t *len);
int wire_ack_put(uint8_t *out, size_t cap, const wire_peer_t *p, uint32_t session, uint32_t seq, uint8_t flags);
int wire_ack_get(const uint8_t *d, size_t n, wire_ack_t *a);

void wire_out_init(wire_out_t *o, uint8_t *buf, size_t cap, uint32_t seq);
int wire_out_add(wire_out_t *o, uint32_t stamp, const char *line, size_t n);
int wire_out_frame(wire_out_t *o, uint8_t *out, size_t cap, const wire_data_t *head);
int wire_out_poll(const wire_out_t *o, uint8_t *out, size_t cap, const wire_data_t *h);
int wire_out_ack(wire_out_t *o, uint32_t seq, uint8_t flags);
void wire_out_missed(wire_out_t *o);

int wire_seen_fresh(wire_seen_t *s, uint8_t id);

void wire_peer_init(wire_peer_t *p, const uint8_t mac[6], uint8_t state, uint32_t now);
int wire_peer_data(wire_peer_t *p, const wire_data_t *d, int8_t rssi, uint32_t now);
int wire_peer_cmd(wire_peer_t *p, const char *line, size_t n);
void wire_peer_node(const wire_peer_t *p, uint32_t now, wire_node_t *n);

void wire_pair_open(wire_pair_t *p, uint32_t now, unsigned seconds);
unsigned wire_pair_left(wire_pair_t *p, uint32_t now);
wire_cand_t *wire_pair_heard(wire_pair_t *p, const uint8_t mac[6], int8_t rssi, const wire_join_t *j, uint32_t now,
                             int *tell);
wire_cand_t *wire_pair_find(wire_pair_t *p, const uint8_t mac[6], uint32_t now);

void wire_air_init(wire_air_t *a, int paired, uint8_t home);
uint8_t wire_air_next(wire_air_t *a, uint32_t now);
void wire_air_wait(wire_air_t *a, uint8_t ch, uint32_t now);
void wire_air_linked(wire_air_t *a, uint8_t ch, uint32_t now);
int wire_air_lost(wire_air_t *a, uint32_t now);
void wire_air_answer(wire_air_t *a, uint8_t flags);
int wire_air_run(wire_air_t *a);

int wire_tx_ready(wire_tx_t *t, uint32_t now);
void wire_tx_issued(wire_tx_t *t, uint32_t now);
void wire_tx_done(wire_tx_t *t, int ok);
int wire_tx_result(const wire_tx_t *t);

int wire_button(wire_button_t *b, int level, uint32_t now);

void wire_chunk(uint16_t *c2, uint32_t *c5, unsigned n, uint16_t *o2, uint32_t *o5);
unsigned wire_chunk_size(unsigned dwell);

const char *wire_hub_args(const proto_cmd_t *c, uint8_t *home, uint8_t key[WIRE_KEY]);
const char *wire_pair_args(const proto_cmd_t *c, unsigned *seconds);
const char *wire_to_args(const proto_cmd_t *c, uint8_t mac[6], char *line, size_t cap);
int wire_remote_ok(const char *name);

int wire_relay(char *out, size_t cap, const uint8_t mac[6], const char *line, size_t n, uint32_t extra);
int wire_unrelay(const char *in, size_t n, char id[13], char *out, size_t cap);
int wire_cluster_line(char *out, size_t cap, uint32_t seq, const wire_cluster_t *c);
int wire_node_line(char *out, size_t cap, uint32_t seq, const wire_node_t *n);
int wire_join_line(char *out, size_t cap, uint32_t seq, const wire_joiner_t *j);
