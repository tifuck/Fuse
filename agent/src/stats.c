/* Fuse connector: live machine usage (CPU, RAM, disk, network) for the
 * chat's stats card. See stats.h. */
#include <dirent.h>
#include <fcntl.h>
#include <sys/statvfs.h>

#include "outq.h"
#include "stats.h"

static int s_on = 0;
static int s_interval_ms = 2000;
static int64_t s_due_ms = 0;
static int s_have_prev = 0;
static struct stats_sample s_prev;
static int s_fd_cpu = -1, s_fd_mem = -1, s_fd_net = -1, s_fd_disk = -1;
static char s_nets[STATS_MAX_DEVS][32];
static int s_n_nets = 0;
static char s_disks[STATS_MAX_DEVS][32];
static int s_n_disks = 0;
/* /proc/net/dev and /proc/diskstats grow with veths and loop devices. */
static char s_buf[65536];

static int name_at(char names[][32], int n, const char *name, size_t len) {
    if (len >= 32) return -1;
    for (int i = 0; i < n; i++)
        if (strncmp(names[i], name, len) == 0 && names[i][len] == 0) return i;
    return -1;
}

int stats_parse_cpu(const char *buf, uint64_t *busy, uint64_t *total) {
    unsigned long long v[8] = {0};
    if (sscanf(buf, "cpu %llu %llu %llu %llu %llu %llu %llu %llu",
               &v[0], &v[1], &v[2], &v[3], &v[4], &v[5], &v[6], &v[7]) < 4)
        return -1;
    uint64_t t = 0;
    for (int i = 0; i < 8; i++) t += v[i]; /* guest time is already in user */
    *total = t;
    *busy = t - v[3] - v[4];               /* idle and iowait */
    return 0;
}

int stats_parse_mem(const char *buf, uint64_t *used, uint64_t *total) {
    const char *t = strstr(buf, "MemTotal:"), *a = strstr(buf, "MemAvailable:");
    if (!t || !a) return -1;
    unsigned long long tk = strtoull(t + 9, NULL, 10), ak = strtoull(a + 13, NULL, 10);
    if (!tk || ak > tk) return -1;
    *total = tk * 1024;
    *used = (tk - ak) * 1024;
    return 0;
}

/* Interfaces in names count; with none listed (a container sees only
 * virtual ones) every interface but loopback does. */
int stats_parse_net(const char *buf, char names[][32], int n, uint64_t *rx, uint64_t *tx) {
    *rx = *tx = 0;
    for (const char *line = buf; line && *line; line = strchr(line, '\n'), line = line ? line + 1 : NULL) {
        const char *colon = strchr(line, ':'), *eol = strchr(line, '\n');
        if (!colon || (eol && colon > eol)) continue;
        const char *name = line;
        while (*name == ' ') name++;
        size_t len = (size_t)(colon - name);
        if (n > 0 ? name_at(names, n, name, len) < 0 : (len == 2 && strncmp(name, "lo", 2) == 0)) continue;
        unsigned long long f[9];
        if (sscanf(colon + 1, "%llu %llu %llu %llu %llu %llu %llu %llu %llu",
                   &f[0], &f[1], &f[2], &f[3], &f[4], &f[5], &f[6], &f[7], &f[8]) != 9)
            continue;
        *rx += f[0];
        *tx += f[8];
    }
    return 0;
}

/* Whole disks in names only: partitions, loop, dm and md devices would
 * count the same I/O twice. io_ms[i] is names[i]'s time spent doing I/O. */
int stats_parse_disk(const char *buf, char names[][32], int n, uint64_t *r, uint64_t *w, uint64_t *io_ms) {
    *r = *w = 0;
    for (int i = 0; i < n; i++) io_ms[i] = 0;
    for (const char *line = buf; line && *line; line = strchr(line, '\n'), line = line ? line + 1 : NULL) {
        char name[32];
        unsigned long long f[10];
        if (sscanf(line, " %*u %*u %31s %llu %llu %llu %llu %llu %llu %llu %llu %llu %llu", name,
                   &f[0], &f[1], &f[2], &f[3], &f[4], &f[5], &f[6], &f[7], &f[8], &f[9]) != 11)
            continue;
        int i = name_at(names, n, name, strlen(name));
        if (i < 0) continue;
        *r += f[2] * 512; /* sectors are 512 bytes in diskstats whatever the device */
        *w += f[6] * 512;
        io_ms[i] = f[9];
    }
    return 0;
}

static uint64_t delta(uint64_t a, uint64_t b) { return b > a ? b - a : 0; }

int stats_format(char *dst, size_t cap, const struct stats_sample *a, const struct stats_sample *b,
                 uint64_t vol_used, uint64_t vol_total) {
    double secs = (double)(b->at_ms - a->at_ms) / 1000.0;
    if (secs <= 0) return -1;
    uint64_t dt = delta(a->cpu_total, b->cpu_total);
    double cpu = dt ? 100.0 * (double)delta(a->cpu_busy, b->cpu_busy) / (double)dt : 0;
    if (cpu > 100) cpu = 100;
    int n = snprintf(dst, cap, "{\"v\":1,\"t\":\"stats\",\"cpu\":%.1f,\"mem\":[%llu,%llu],\"net\":[%.0f,%.0f]",
                     cpu, (unsigned long long)b->mem_used, (unsigned long long)b->mem_total,
                     (double)delta(a->net_rx, b->net_rx) / secs, (double)delta(a->net_tx, b->net_tx) / secs);
    if (b->n_disks > 0 && n > 0 && (size_t)n < cap) {
        double busy = 0;
        for (int i = 0; i < b->n_disks; i++) {
            double p = 100.0 * (double)delta(a->disk_io_ms[i], b->disk_io_ms[i]) / (secs * 1000.0);
            if (p > busy) busy = p;
        }
        if (busy > 100) busy = 100;
        n += snprintf(dst + n, cap - (size_t)n, ",\"disk\":[%.1f,%.0f,%.0f]", busy,
                      (double)delta(a->disk_r, b->disk_r) / secs, (double)delta(a->disk_w, b->disk_w) / secs);
    }
    if (vol_total && n > 0 && (size_t)n < cap)
        n += snprintf(dst + n, cap - (size_t)n, ",\"vol\":[%llu,%llu]",
                      (unsigned long long)vol_used, (unsigned long long)vol_total);
    if (n > 0 && (size_t)n < cap) n += snprintf(dst + n, cap - (size_t)n, "}");
    return n > 0 && (size_t)n < cap ? n : -1;
}

/* Physical devices: those backed by hardware (a "device" link), so
 * bridges, veths, tunnels, loop and dm devices are left out. */
static int list_devices(const char *dir, char names[][32]) {
    DIR *d = opendir(dir);
    if (!d) return 0;
    int n = 0, dfd = dirfd(d);
    struct dirent *e;
    char path[64];
    while (n < STATS_MAX_DEVS && (e = readdir(d))) {
        if (e->d_name[0] == '.' || strlen(e->d_name) >= 32) continue;
        snprintf(path, sizeof(path), "%s/device", e->d_name);
        if (faccessat(dfd, path, F_OK, 0) == 0) snprintf(names[n++], 32, "%s", e->d_name);
    }
    closedir(d);
    return n;
}

static void close_fd(int *fd) {
    if (*fd >= 0) close(*fd);
    *fd = -1;
}

/* proc files stay open while sampling; pread at 0 re-reads them fresh. */
static const char *read_proc(int fd, size_t want) {
    if (fd < 0) return NULL;
    if (want > sizeof(s_buf) - 1) want = sizeof(s_buf) - 1;
    ssize_t n = pread(fd, s_buf, want, 0);
    if (n <= 0) return NULL;
    s_buf[n] = 0;
    return s_buf;
}

static void sample(struct stats_sample *s, int64_t now_ms) {
    memset(s, 0, sizeof(*s));
    s->at_ms = now_ms;
    s->n_disks = s_n_disks;
    const char *b;
    /* Only the first line of /proc/stat and the first three of meminfo matter. */
    if ((b = read_proc(s_fd_cpu, 512))) stats_parse_cpu(b, &s->cpu_busy, &s->cpu_total);
    if ((b = read_proc(s_fd_mem, 512))) stats_parse_mem(b, &s->mem_used, &s->mem_total);
    if ((b = read_proc(s_fd_net, sizeof(s_buf)))) stats_parse_net(b, s_nets, s_n_nets, &s->net_rx, &s->net_tx);
    if ((b = read_proc(s_fd_disk, sizeof(s_buf))))
        stats_parse_disk(b, s_disks, s_n_disks, &s->disk_r, &s->disk_w, s->disk_io_ms);
}

void stats_reset(void) {
    s_on = 0;
    s_have_prev = 0;
    close_fd(&s_fd_cpu);
    close_fd(&s_fd_mem);
    close_fd(&s_fd_net);
    close_fd(&s_fd_disk);
}

void stats_set(int on, int interval_ms, int64_t now_ms) {
    if (!on) {
        stats_reset();
        return;
    }
    if (interval_ms < 1000) interval_ms = 1000;
    if (interval_ms > 10000) interval_ms = 10000;
    s_interval_ms = interval_ms;
    if (s_on) return;
    s_on = 1;
    s_fd_cpu = open("/proc/stat", O_RDONLY | O_CLOEXEC);
    s_fd_mem = open("/proc/meminfo", O_RDONLY | O_CLOEXEC);
    s_fd_net = open("/proc/net/dev", O_RDONLY | O_CLOEXEC);
    s_fd_disk = open("/proc/diskstats", O_RDONLY | O_CLOEXEC);
    s_n_nets = list_devices("/sys/class/net", s_nets);
    s_n_disks = list_devices("/sys/block", s_disks);
    sample(&s_prev, now_ms);
    s_have_prev = 1;
    /* The first frame comes a second after the baseline, not a whole interval. */
    s_due_ms = now_ms + 1000;
}

int stats_wait_ms(int64_t now_ms, int cap) {
    if (!s_on) return cap;
    int64_t left = s_due_ms - now_ms;
    if (left < 0) left = 0;
    return left < cap ? (int)left : cap;
}

void stats_tick(int64_t now_ms) {
    if (!s_on || now_ms < s_due_ms) return;
    s_due_ms = now_ms + s_interval_ms;
    struct stats_sample cur;
    sample(&cur, now_ms);
    if (s_have_prev) {
        uint64_t vol_used = 0, vol_total = 0;
        struct statvfs v;
        /* shortcut: the root volume only; per-project volumes need the path from the server. */
        if (statvfs("/", &v) == 0) {
            vol_total = (uint64_t)v.f_blocks * v.f_frsize;
            vol_used = (uint64_t)(v.f_blocks - v.f_bfree) * v.f_frsize;
        }
        char frame[512];
        int n = stats_format(frame, sizeof(frame), &s_prev, &cur, vol_used, vol_total);
        /* Lossy: never take the room results and job output need. */
        if (n > 0 && out_has_room()) out_enqueue(1, (const unsigned char *)frame, (size_t)n);
    }
    s_prev = cur;
    s_have_prev = 1;
}
