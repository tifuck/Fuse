#ifndef FUSE_STATS_H
#define FUSE_STATS_H

/* Live machine usage for the chat's stats card. Samples only while the
 * server has asked for it ("stats" on), on the session thread: a few
 * preads of /proc per tick, no thread, nothing at all while off. */

#include <stdint.h>

#include "agent.h"

#define STATS_MAX_DEVS 32

struct stats_sample {
    uint64_t cpu_busy, cpu_total;      /* jiffies */
    uint64_t mem_used, mem_total;      /* bytes */
    uint64_t net_rx, net_tx;           /* bytes, summed */
    uint64_t disk_r, disk_w;           /* bytes, summed */
    uint64_t disk_io_ms[STATS_MAX_DEVS]; /* per disk busy ms */
    int n_disks;                       /* 0: no physical disk seen, no disk field */
    int64_t at_ms;
};

/* Session thread only. */
void stats_set(int on, int interval_ms, int64_t now_ms);
void stats_reset(void);
/* cap, or less when a sample falls due sooner. */
int stats_wait_ms(int64_t now_ms, int cap);
void stats_tick(int64_t now_ms);

/* Parsers over /proc text, exposed for tests. keep() picks the devices
 * that count (names in a list); NULL keeps every one. */
int stats_parse_cpu(const char *buf, uint64_t *busy, uint64_t *total);
int stats_parse_mem(const char *buf, uint64_t *used, uint64_t *total);
int stats_parse_net(const char *buf, char names[][32], int n, uint64_t *rx, uint64_t *tx);
int stats_parse_disk(const char *buf, char names[][32], int n, uint64_t *r, uint64_t *w, uint64_t *io_ms);
/* The frame for two samples; returns its length or -1. */
int stats_format(char *dst, size_t cap, const struct stats_sample *a, const struct stats_sample *b,
                 uint64_t vol_used, uint64_t vol_total);

#endif
