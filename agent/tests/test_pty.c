/* Terminals: binary framing, the per-PTY output window and input bound, and
 * real PTYs running /bin/sh -c scripts (no login shell): pty_ok before any
 * output, resize, window pause and resume on ack, exit code, cancel with
 * SIGHUP then SIGKILL, the 16-terminal cap. */
#include "../src/agent.h"
#include "../src/jobs.h"
#include "../src/outq.h"
#include "../src/pty.h"
#include <assert.h>
#include <pthread.h>
#include <string.h>
#include <time.h>

extern char **environ;

#define TRACK 24
struct seen {
    char id[PTY_ID_LEN + 1];
    int ok, done, res_ok, exit; /* exit -1: null */
    char error[128];
    size_t n;
    char out[700000];
};
static struct seen g_seen[TRACK];
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;
static volatile int g_stop = 0;

static struct seen *seen_for(const char *id) {
    for (int i = 0; i < TRACK; i++)
        if (!strcmp(g_seen[i].id, id)) return &g_seen[i];
    for (int i = 0; i < TRACK; i++)
        if (!g_seen[i].id[0]) {
            snprintf(g_seen[i].id, sizeof(g_seen[i].id), "%s", id);
            return &g_seen[i];
        }
    assert(0);
    return NULL;
}

static int record(void *ctx, int opcode, const unsigned char *data, size_t n) {
    (void)ctx;
    pthread_mutex_lock(&g_mu);
    if (opcode == 1) {
        cJSON *o = cJSON_ParseWithLength((const char *)data, n);
        assert(!strcmp(json_cstr(o, "t", ""), "pty_ok"));
        assert(cJSON_IsNumber(cJSON_GetObjectItem(o, "pid")));
        assert(!strcmp(json_cstr(o, "shell", ""), "/bin/sh"));
        seen_for(json_cstr(o, "id", ""))->ok = 1;
        cJSON_Delete(o);
    } else {
        char id[PTY_ID_LEN + 1];
        const unsigned char *p;
        size_t pn;
        assert(opcode == 2);
        assert(pty_frame_parse(data, n, id, &p, &pn) == PTY_KIND_OUT);
        assert(pn > 0 && pn <= PTY_FRAME_MAX);
        struct seen *s = seen_for(id);
        assert(s->ok); /* pty_ok goes first */
        assert(s->n + pn <= sizeof(s->out));
        memcpy(s->out + s->n, p, pn);
        s->n += pn;
    }
    pthread_mutex_unlock(&g_mu);
    return 0;
}

static void *session(void *unused) {
    (void)unused;
    struct timespec pause = {0, 1000000};
    while (!g_stop) {
        assert(pty_flush_with(record, NULL) == 0);
        pthread_mutex_lock(&g_out_mu);
        if (g_out_n == 0) {
            pthread_mutex_unlock(&g_out_mu);
            if (!pty_pending()) nanosleep(&pause, NULL);
            continue;
        }
        unsigned char *data = g_outq[g_out_head].data;
        size_t n = g_outq[g_out_head].n;
        g_outq[g_out_head].data = NULL;
        g_out_head = (g_out_head + 1) % OUT_QMAX;
        g_out_n--;
        pthread_cond_broadcast(&g_out_space);
        pthread_mutex_unlock(&g_out_mu);
        cJSON *o = cJSON_ParseWithLength((const char *)data, n);
        assert(!strcmp(json_cstr(o, "t", ""), "res"));
        pthread_mutex_lock(&g_mu);
        struct seen *s = seen_for(json_cstr(o, "id", ""));
        cJSON *ex = cJSON_GetObjectItem(o, "exit");
        s->res_ok = cJSON_IsTrue(cJSON_GetObjectItem(o, "ok"));
        s->exit = cJSON_IsNumber(ex) ? ex->valueint : -1;
        snprintf(s->error, sizeof(s->error), "%s", json_cstr(o, "error", ""));
        s->done = 1;
        pthread_mutex_unlock(&g_mu);
        cJSON_Delete(o);
        free(data);
    }
    return NULL;
}

struct open_args {
    char id[PTY_ID_LEN + 1];
    const char *path;
    const char *script;
    struct job *job;
};

static void *open_pty(void *arg) {
    struct open_args *a = arg;
    char *const argv[] = {"sh", "-c", (char *)a->script, NULL};
    pty_session(a->id, 80, 24, "/tmp", "/", a->path, argv, environ, a->job);
    return NULL;
}

static double mono_s(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

/* Waits up to 10 s for cond on the record of id. */
#define WAIT_FOR(id, cond)                                         \
    do {                                                           \
        double until_ = mono_s() + 10;                             \
        for (;;) {                                                 \
            pthread_mutex_lock(&g_mu);                             \
            struct seen *s = seen_for(id);                         \
            int ok_ = (cond);                                      \
            pthread_mutex_unlock(&g_mu);                           \
            if (ok_) break;                                        \
            assert(mono_s() < until_);                             \
            nanosleep(&(struct timespec){0, 2000000}, NULL);       \
        }                                                          \
    } while (0)

static void cancel(const char *id) {
    struct job *j = job_reserve(id);
    pthread_mutex_lock(&g_job_mu);
    j->cancel = 1;
    pthread_mutex_unlock(&g_job_mu);
    pty_wake(id);
}

static pthread_t start(struct open_args *a, const char *id, const char *script, struct job *job) {
    snprintf(a->id, sizeof(a->id), "%s", id);
    a->path = "/bin/sh";
    a->script = script;
    a->job = job;
    pthread_t th;
    assert(pthread_create(&th, NULL, open_pty, a) == 0);
    return th;
}

static void input(const char *id, const char *text) {
    unsigned char frame[PTY_HDR + 64];
    size_t n = pty_frame_build(frame, PTY_KIND_IN, id, (const unsigned char *)text, strlen(text));
    pty_input(frame, n);
}

int main(void) {
    /* Framing: kind, 12-char hex id, payload; anything else is malformed. */
    unsigned char frame[PTY_HDR + PTY_FRAME_MAX + 1];
    char id[PTY_ID_LEN + 1];
    const unsigned char *p;
    size_t pn;
    size_t n = pty_frame_build(frame, PTY_KIND_OUT, "0123456789aB", (const unsigned char *)"hi\0x", 4);
    assert(n == PTY_HDR + 4 && frame[0] == 0x01 && !memcmp(frame + 1, "0123456789aB", 12));
    assert(pty_frame_parse(frame, n, id, &p, &pn) == PTY_KIND_OUT);
    assert(!strcmp(id, "0123456789aB") && pn == 4 && !memcmp(p, "hi\0x", 4));
    assert(pty_frame_parse(frame, PTY_HDR, id, &p, &pn) == PTY_KIND_OUT && pn == 0);
    assert(pty_frame_parse(frame, PTY_HDR - 1, id, &p, &pn) == -1);
    assert(pty_frame_parse(NULL, 0, id, &p, &pn) == -1);
    frame[5] = 'g';
    assert(pty_frame_parse(frame, n, id, &p, &pn) == -1);
    frame[5] = 0;
    assert(pty_frame_parse(frame, n, id, &p, &pn) == -1);
    n = pty_frame_build(frame, PTY_KIND_IN, "abcdefabcdef", frame, 0);
    memset(frame + PTY_HDR, 'x', PTY_FRAME_MAX + 1);
    assert(pty_frame_parse(frame, PTY_HDR + PTY_FRAME_MAX, id, &p, &pn) == PTY_KIND_IN && pn == PTY_FRAME_MAX);
    assert(pty_frame_parse(frame, PTY_HDR + PTY_FRAME_MAX + 1, id, &p, &pn) == -1);
    assert(!pty_id_ok("0123456789a") && !pty_id_ok("0123456789abc") && !pty_id_ok(NULL));
    pty_input(frame, PTY_HDR + 3); /* no such terminal: ignored */

    /* Window: reading stops once 512 KiB are unacked, resumes as acks come. */
    assert(pty_window_left(0, 0) == PTY_WINDOW);
    assert(pty_window_left(PTY_WINDOW - 1, 0) == 1);
    assert(pty_window_left(PTY_WINDOW, 0) == 0);
    assert(pty_window_left(PTY_WINDOW + 4096, 0) == 0);
    assert(pty_window_left(PTY_WINDOW + 4096, 8192) == 4096);
    assert(pty_window_left(10, 20) == PTY_WINDOW);

    /* Input bound: a frame is taken whole or not at all. */
    static unsigned char in[PTY_IN_MAX];
    size_t len = 0;
    assert(pty_buf_put(in, &len, PTY_IN_MAX, frame, PTY_FRAME_MAX) && len == PTY_FRAME_MAX);
    while (pty_buf_put(in, &len, PTY_IN_MAX, frame, PTY_FRAME_MAX)) {
    }
    assert(len == PTY_IN_MAX);
    assert(!pty_buf_put(in, &len, PTY_IN_MAX, frame, 1) && len == PTY_IN_MAX);
    assert(pty_buf_put(in, &len, PTY_IN_MAX, frame, 0));
    len = PTY_IN_MAX - 10;
    assert(!pty_buf_put(in, &len, PTY_IN_MAX, frame, 11) && len == PTY_IN_MAX - 10);
    assert(pty_buf_put(in, &len, PTY_IN_MAX, frame, 10) && len == PTY_IN_MAX);

    pthread_t drain;
    assert(pthread_create(&drain, NULL, session, NULL) == 0);

    /* A shell: output, resize (stty sees it), input, exit code. */
    struct open_args a;
    pthread_t th = start(&a, "a0000000000a", "printf 'hello\\n'; read x; stty size; exit 3", NULL);
    WAIT_FOR("a0000000000a", memmem(s->out, s->n, "hello", 5) != NULL);
    pty_resize("a0000000000a", 100, 30);
    input("a0000000000a", "\n");
    WAIT_FOR("a0000000000a", s->done);
    pthread_join(th, NULL);
    pthread_mutex_lock(&g_mu);
    struct seen *s = seen_for("a0000000000a");
    assert(s->res_ok && s->exit == 3);
    assert(memmem(s->out, s->n, "30 100", 6));
    pthread_mutex_unlock(&g_mu);

    /* Flood: the master is left unread at exactly 512 KiB unacked. */
    th = start(&a, "b0000000000b", "head -c 600000 /dev/zero | tr '\\000' x; exit 0", NULL);
    WAIT_FOR("b0000000000b", s->n == PTY_WINDOW);
    nanosleep(&(struct timespec){0, 300000000}, NULL);
    pthread_mutex_lock(&g_mu);
    assert(seen_for("b0000000000b")->n == PTY_WINDOW && !seen_for("b0000000000b")->done);
    pthread_mutex_unlock(&g_mu);
    pty_ack("b0000000000b", PTY_WINDOW);
    WAIT_FOR("b0000000000b", s->done);
    pthread_join(th, NULL);
    pthread_mutex_lock(&g_mu);
    s = seen_for("b0000000000b");
    assert(s->res_ok && s->exit == 0 && s->n == 600000);
    for (size_t i = 0; i < s->n; i++) assert(s->out[i] == 'x');
    pthread_mutex_unlock(&g_mu);

    /* A background process still holding the terminal does not delay the
     * res, and the last output still arrives before it. */
    double w0 = mono_s();
    th = start(&a, "b1000000000b", "(sleep 5 &); printf bye; exit 4", NULL);
    WAIT_FOR("b1000000000b", s->done);
    pthread_join(th, NULL);
    assert(mono_s() - w0 < 1.0);
    pthread_mutex_lock(&g_mu);
    s = seen_for("b1000000000b");
    assert(s->res_ok && s->exit == 4 && memmem(s->out, s->n, "bye", 3));
    pthread_mutex_unlock(&g_mu);

    /* Cancel: SIGHUP to the group; one that ignores it is SIGKILLed 2 s later. */
    struct job *job = job_reserve("c0000000000c");
    th = start(&a, "c0000000000c", "trap '' HUP; printf ready; sleep 30", job);
    WAIT_FOR("c0000000000c", s->n >= 5);
    double t0 = mono_s();
    cancel("c0000000000c");
    WAIT_FOR("c0000000000c", s->done);
    double took = mono_s() - t0;
    pthread_join(th, NULL);
    assert(took >= PTY_KILL_MS / 1000.0 - 0.05 && took < PTY_KILL_MS / 1000.0 + 1.5);
    pthread_mutex_lock(&g_mu);
    assert(seen_for("c0000000000c")->exit == 137);
    pthread_mutex_unlock(&g_mu);

    /* A shell that cannot start fails the open, with no pty_ok. */
    struct open_args bad = {"e0000000000e", "/nonexistent/sh", "true", NULL};
    open_pty(&bad);
    WAIT_FOR("e0000000000e", s->done);
    pthread_mutex_lock(&g_mu);
    s = seen_for("e0000000000e");
    assert(!s->res_ok && !s->ok && strstr(s->error, "could not start /nonexistent/sh"));
    pthread_mutex_unlock(&g_mu);

    /* At most 16 terminals; the 17th is refused at once. */
    static struct open_args many[PTY_MAX];
    pthread_t ths[PTY_MAX];
    char ids[PTY_MAX][PTY_ID_LEN + 1];
    for (int i = 0; i < PTY_MAX; i++) {
        strcpy(ids[i], "d0000000000d");
        ids[i][10] = "0123456789abcdef"[i];
        ths[i] = start(&many[i], ids[i], "exec sleep 30", job_reserve(ids[i]));
    }
    for (int i = 0; i < PTY_MAX; i++) WAIT_FOR(ids[i], s->ok);
    struct open_args extra = {"f0000000000f", "/bin/sh", "true", NULL};
    open_pty(&extra);
    WAIT_FOR("f0000000000f", s->done);
    pthread_mutex_lock(&g_mu);
    s = seen_for("f0000000000f");
    assert(!s->res_ok && strstr(s->error, "too many terminals"));
    pthread_mutex_unlock(&g_mu);
    for (int i = 0; i < PTY_MAX; i++) cancel(ids[i]);
    for (int i = 0; i < PTY_MAX; i++) {
        WAIT_FOR(ids[i], s->done && s->exit == 129); /* SIGHUP */
        pthread_join(ths[i], NULL);
    }
    assert(!pty_pending());

    g_stop = 1;
    pthread_join(drain, NULL);
    printf("test_pty: OK\n");
    return 0;
}
