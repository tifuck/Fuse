/* Stats card sampling: the /proc parsers on fixture text, the frame's rates
 * and percentages, and that sampling stays off until asked. */
#include "../src/agent.h"
#include "../src/outq.h"
#include "../src/stats.h"
#include <assert.h>

static const char *STAT =
    "cpu  100 0 50 800 50 0 0 0 30 0\n"
    "cpu0 100 0 50 800 50 0 0 0 30 0\n";

static const char *MEMINFO =
    "MemTotal:       16000000 kB\n"
    "MemFree:         2000000 kB\n"
    "MemAvailable:   10000000 kB\n";

static const char *NETDEV =
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
    "    lo: 999999 10 0 0 0 0 0 0 999999 10 0 0 0 0 0 0\n"
    "  eth0: 1000 10 0 0 0 0 0 0 2000 20 0 0 0 0 0 0\n"
    "docker0: 500 5 0 0 0 0 0 0 700 7 0 0 0 0 0 0\n";

static const char *DISKSTATS =
    "   8       0 sda 100 0 2000 50 200 0 4000 80 0 120 130 0 0 0 0\n"
    "   8       1 sda1 100 0 2000 50 200 0 4000 80 0 120 130 0 0 0 0\n"
    "   7       0 loop0 9 0 9999 1 0 0 0 0 0 1 1 0 0 0 0\n"
    " 259       0 nvme0n1 10 0 100 5 20 0 200 8 0 40 45 0 0 0 0\n";

int main(void) {
    uint64_t a, b;
    assert(stats_parse_cpu(STAT, &a, &b) == 0);
    assert(b == 1000 && a == 150); /* idle and iowait are not busy; guest is not added twice */
    assert(stats_parse_cpu("intr 1 2 3\n", &a, &b) == -1);

    assert(stats_parse_mem(MEMINFO, &a, &b) == 0);
    assert(b == 16000000ULL * 1024 && a == 6000000ULL * 1024);
    assert(stats_parse_mem("MemTotal: 10 kB\n", &a, &b) == -1);

    char eth[][32] = {"eth0"};
    assert(stats_parse_net(NETDEV, eth, 1, &a, &b) == 0);
    assert(a == 1000 && b == 2000); /* only listed (physical) interfaces */
    assert(stats_parse_net(NETDEV, NULL, 0, &a, &b) == 0);
    assert(a == 1500 && b == 2700); /* none listed: every one but loopback */

    char disks[][32] = {"sda", "nvme0n1"};
    uint64_t io[2];
    assert(stats_parse_disk(DISKSTATS, disks, 2, &a, &b, io) == 0);
    assert(a == (2000 + 100) * 512ULL && b == (4000 + 200) * 512ULL); /* no partitions, no loop */
    assert(io[0] == 120 && io[1] == 40);

    struct stats_sample s0 = {0}, s1 = {0};
    s0.at_ms = 0;
    s1.at_ms = 2000;
    s0.cpu_total = 1000; s0.cpu_busy = 100;
    s1.cpu_total = 1400; s1.cpu_busy = 200;          /* 25% */
    s1.mem_used = 6; s1.mem_total = 16;
    s0.net_rx = 0; s1.net_rx = 4000;                  /* 2000 B/s */
    s0.net_tx = 500; s1.net_tx = 100;                 /* counter reset: 0, never negative */
    s0.n_disks = s1.n_disks = 2;
    s0.disk_io_ms[0] = 0; s1.disk_io_ms[0] = 500;     /* 25% busy */
    s0.disk_io_ms[1] = 0; s1.disk_io_ms[1] = 1800;    /* 90%: the busiest disk wins */
    s1.disk_r = 1000; s1.disk_w = 3000;
    char out[512];
    assert(stats_format(out, sizeof(out), &s0, &s1, 7, 9) > 0);
    assert(strcmp(out, "{\"v\":1,\"t\":\"stats\",\"cpu\":25.0,\"mem\":[6,16],\"net\":[2000,0],"
                       "\"disk\":[90.0,500,1500],\"vol\":[7,9]}") == 0);
    s1.n_disks = 0;
    assert(stats_format(out, sizeof(out), &s0, &s1, 0, 0) > 0);
    assert(!strstr(out, "disk") && !strstr(out, "vol")); /* nothing seen, nothing sent */
    assert(stats_format(out, 8, &s0, &s1, 0, 0) == -1);
    s1.at_ms = 0;
    assert(stats_format(out, sizeof(out), &s0, &s1, 0, 0) == -1);

    /* Off: no wait shortening, no frame. On: a frame a second after the baseline. */
    assert(stats_wait_ms(0, 1000) == 1000);
    stats_tick(5000);
    assert(g_out_n == 0);
    stats_set(1, 2000, 10000);
    assert(stats_wait_ms(10000, 5000) == 1000);
    stats_tick(10500);
    assert(g_out_n == 0);
    stats_tick(11000);
    assert(g_out_n == 1);
    assert(strstr((const char *)g_outq[g_out_head].data, "\"t\":\"stats\""));
    assert(stats_wait_ms(11000, 5000) == 2000);
    stats_set(0, 0, 11000);
    assert(stats_wait_ms(11000, 1000) == 1000);
    stats_tick(20000);
    assert(g_out_n == 1);

    puts("test_stats: ok");
    return 0;
}
