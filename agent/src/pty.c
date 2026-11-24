/* Fuse connector: interactive terminals (pty_open jobs). */
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <pwd.h>
#include <signal.h>
#include <sys/eventfd.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <termios.h>

#include "agent.h"
#include "exec.h"
#include "files.h"
#include "jobs.h"
#include "net.h"
#include "outq.h"
#include "pty.h"
#include "reconnect.h"

/* One open terminal. The worker running it owns the master fd, the pid and
 * the pidfd; everything here is shared with the network thread under
 * g_pty_mu. Only the worker removes bytes from in[] and only the network
 * thread removes them from out[], so each side reads its own end unlocked. */
struct pty {
    char id[PTY_ID_LEN + 1];
    unsigned gen;    /* tells a reused slot apart */
    int wake;        /* eventfd: input, ack, resize, cancel or output sent */
    uint64_t sent;   /* output bytes read from the master */
    uint64_t acked;  /* of those, acked by the server */
    int cols, rows, resize;
    int in_dropped;  /* logged once */
    char *ok;        /* pty_ok frame not yet sent: goes before any output */
    size_t out_len, in_len;
    unsigned char out[PTY_OUT_MAX];
    unsigned char in[PTY_IN_MAX];
};

static struct pty g_ptys[PTY_MAX];
static pthread_mutex_t g_pty_mu = PTHREAD_MUTEX_INITIALIZER;
static unsigned g_pty_gen = 0;

int pty_id_ok(const char *id) {
    return id && strlen(id) == PTY_ID_LEN && strspn(id, "0123456789abcdefABCDEF") == PTY_ID_LEN;
}

size_t pty_frame_build(unsigned char *out, int kind, const char *id, const unsigned char *data, size_t n) {
    out[0] = (unsigned char)kind;
    memcpy(out + 1, id, PTY_ID_LEN);
    if (n) memcpy(out + PTY_HDR, data, n);
    return PTY_HDR + n;
}

int pty_frame_parse(const unsigned char *buf, size_t n, char id[PTY_ID_LEN + 1],
                    const unsigned char **data, size_t *dn) {
    if (!buf || n < PTY_HDR || n - PTY_HDR > PTY_FRAME_MAX) return -1;
    memcpy(id, buf + 1, PTY_ID_LEN);
    id[PTY_ID_LEN] = 0;
    if (!pty_id_ok(id)) return -1;
    *data = buf + PTY_HDR;
    *dn = n - PTY_HDR;
    return buf[0];
}

size_t pty_window_left(uint64_t sent, uint64_t acked) {
    uint64_t used = sent > acked ? sent - acked : 0;
    return used >= PTY_WINDOW ? 0 : (size_t)(PTY_WINDOW - used);
}

int pty_buf_put(unsigned char *buf, size_t *len, size_t cap, const unsigned char *data, size_t n) {
    if (n > cap - *len) return 0;
    if (n) memcpy(buf + *len, data, n);
    *len += n;
    return 1;
}

/* Caller holds g_pty_mu. */
static struct pty *slot_find(const char *id) {
    if (!pty_id_ok(id)) return NULL;
    for (int i = 0; i < PTY_MAX; i++)
        if (g_ptys[i].id[0] && strcmp(g_ptys[i].id, id) == 0) return &g_ptys[i];
    return NULL;
}

/* Caller holds g_pty_mu. Never blocks: the eventfd is non-blocking. */
static void slot_wake(struct pty *s) {
    uint64_t one = 1;
    if (s->wake >= 0) (void)!write(s->wake, &one, sizeof(one));
}

static struct pty *slot_open(const char *id, int wake, int cols, int rows) {
    struct pty *s = NULL;
    pthread_mutex_lock(&g_pty_mu);
    if (!slot_find(id))
        for (int i = 0; i < PTY_MAX && !s; i++)
            if (!g_ptys[i].id[0]) s = &g_ptys[i];
    if (s) {
        strcpy(s->id, id); /* pty_id_ok checked the length */
        s->gen = ++g_pty_gen;
        s->wake = wake;
        s->sent = s->acked = 0;
        s->cols = cols;
        s->rows = rows;
        s->resize = s->in_dropped = 0;
        s->ok = NULL;
        s->out_len = s->in_len = 0;
    }
    pthread_mutex_unlock(&g_pty_mu);
    return s;
}

static void slot_close(struct pty *s) {
    pthread_mutex_lock(&g_pty_mu);
    free(s->ok);
    s->ok = NULL;
    s->out_len = s->in_len = 0;
    close(s->wake);
    s->wake = -1;
    s->id[0] = 0;
    pthread_mutex_unlock(&g_pty_mu);
}

/* Output the worker read; n fits (the worker sized the read under the lock,
 * and the room only grows meanwhile). */
static void slot_put_out(struct pty *s, const unsigned char *buf, size_t n) {
    pthread_mutex_lock(&g_pty_mu);
    int was_empty = s->out_len == 0;
    memcpy(s->out + s->out_len, buf, n);
    s->out_len += n;
    s->sent += n;
    pthread_mutex_unlock(&g_pty_mu);
    if (was_empty) out_wake();
}

static void wait_wake(int fd, long ms) {
    struct pollfd p = {fd, POLLIN, 0};
    if (poll(&p, 1, (int)(ms < 0 ? 0 : ms)) > 0) {
        uint64_t v;
        (void)!read(fd, &v, sizeof(v));
    }
}

/* A fresh PTY with the shell running on it as a session leader. Every fd is
 * O_CLOEXEC from the start (forkpty's are not): a command another worker
 * forks meanwhile must not inherit a terminal, or the master never sees EOF.
 * Returns the non-blocking master, or -1 with err set. */
static int pty_spawn(const char *path, char *const argv[], char *const envp[], const char *cwd,
                     const char *home, int cols, int rows, pid_t *pid_out, char *err, size_t ecap) {
    char name[64];
    int slave = -1, ep[2] = {-1, -1};
    int master = posix_openpt(O_RDWR | O_NOCTTY | O_CLOEXEC);
    if (master < 0 || grantpt(master) != 0 || unlockpt(master) != 0 || ptsname_r(master, name, sizeof(name)) != 0 ||
        (slave = open(name, O_RDWR | O_NOCTTY | O_CLOEXEC)) < 0 || pipe2(ep, O_CLOEXEC) != 0) {
        snprintf(err, ecap, "could not open a terminal: %s", strerror(errno));
        if (master >= 0) close(master);
        if (slave >= 0) close(slave);
        return -1;
    }
    struct termios tio;
    if (tcgetattr(slave, &tio) == 0) {
        tio.c_iflag |= IUTF8; /* line editing erases whole UTF-8 characters */
        tcsetattr(slave, TCSANOW, &tio);
    }
    struct winsize ws = {.ws_row = (unsigned short)rows, .ws_col = (unsigned short)cols};
    ioctl(master, TIOCSWINSZ, &ws);
    pid_t pid = fork();
    if (pid == 0) {
        sigset_t none;
        sigemptyset(&none);
        sigprocmask(SIG_SETMASK, &none, NULL);
        signal(SIGPIPE, SIG_DFL); /* as exec.c: SIG_IGN would survive exec */
        setsid();
        ioctl(slave, TIOCSCTTY, 0);
        fcntl(slave, F_SETFD, 0);
        dup2(slave, 0);
        dup2(slave, 1);
        dup2(slave, 2);
        if (slave > 2) close(slave);
        if (chdir(cwd) != 0 && chdir(home) != 0) (void)!chdir("/");
        execve(path, argv, envp);
        int e = errno;
        (void)!write(ep[1], &e, sizeof(e));
        _exit(127);
    }
    int fork_err = errno;
    close(slave);
    close(ep[1]);
    if (pid < 0) {
        snprintf(err, ecap, "could not start a shell: %s", strerror(fork_err));
        close(ep[0]);
        close(master);
        return -1;
    }
    /* The status pipe closes on a successful exec; a failed one sends errno. */
    int e = 0;
    ssize_t r;
    while ((r = read(ep[0], &e, sizeof(e))) < 0 && errno == EINTR) {
    }
    close(ep[0]);
    if (r == (ssize_t)sizeof(e)) {
        waitpid(pid, NULL, 0);
        snprintf(err, ecap, "could not start %s: %s", path, strerror(e));
        close(master);
        return -1;
    }
    fcntl(master, F_SETFL, fcntl(master, F_GETFL) | O_NONBLOCK);
    *pid_out = pid;
    return master;
}

/* Moves bytes until the shell is reaped and its buffered output is taken.
 * Returns 1 with status set, or 0 when a SIGKILLed shell never died. */
static int pty_pump(struct pty *s, int master, pid_t pid, int pidfd, struct job *job, int *status) {
    int eof = 0, reaped = 0, hup = 0, killed = 0;
    int64_t hup_ms = 0, reap_ms = 0, quiet_ms = 0;
    unsigned char buf[PTY_FRAME_MAX];
    for (;;) {
        int64_t now = now_mono_ms();
        if (!reaped) {
            if (!hup && job_cancelled(job)) {
                kill(-pid, SIGHUP);
                hup = 1;
                hup_ms = now;
            }
            if (hup && !killed && now - hup_ms >= PTY_KILL_MS) {
                kill(-pid, SIGKILL);
                killed = 1;
            }
            /* As exec.c: a D-state shell must not hold this worker forever. */
            if (killed && now - hup_ms >= PTY_KILL_MS + 10000) {
                fprintf(stderr, "fuse-agent: terminal shell %d unkillable after SIGKILL; abandoning\n", (int)pid);
                return 0;
            }
        }
        pthread_mutex_lock(&g_pty_mu);
        size_t want = eof ? 0 : pty_window_left(s->sent, s->acked);
        if (want > PTY_OUT_MAX - s->out_len) want = PTY_OUT_MAX - s->out_len;
        if (want > sizeof(buf)) want = sizeof(buf);
        size_t in_len = s->in_len;
        struct winsize ws = {.ws_row = (unsigned short)s->rows, .ws_col = (unsigned short)s->cols};
        int resize = s->resize;
        s->resize = 0;
        pthread_mutex_unlock(&g_pty_mu);
        /* Reaped: read on until EOF (the last bytes may still be on their
         * way through the tty), but never wait on a background process that
         * still holds the terminal: PTY_QUIET_MS of nothing ends it. */
        if (reaped) {
            if (!want) quiet_ms = 0;
            else if (!quiet_ms) quiet_ms = now;
            if (eof || now - reap_ms >= 10000 || (quiet_ms && now - quiet_ms >= PTY_QUIET_MS)) return 1;
        }
        if (resize) ioctl(master, TIOCSWINSZ, &ws);
        if (in_len) {
            ssize_t w = write(master, s->in, in_len);
            if (w > 0 || (w < 0 && errno != EAGAIN && errno != EINTR)) {
                size_t done = w > 0 ? (size_t)w : in_len; /* EIO: nothing reads it any more */
                pthread_mutex_lock(&g_pty_mu);
                memmove(s->in, s->in + done, s->in_len - done);
                s->in_len -= done;
                in_len = s->in_len;
                pthread_mutex_unlock(&g_pty_mu);
            }
        }
        struct pollfd p[3];
        int n = 0, mi = -1;
        short ev = (short)((want ? POLLIN : 0) | (in_len ? POLLOUT : 0));
        /* After EOF a master polls as hung up forever: leave it out. */
        if (!eof && ev) {
            mi = n;
            p[n++] = (struct pollfd){master, ev, 0};
        }
        int wi = n;
        p[n++] = (struct pollfd){s->wake, POLLIN, 0};
        if (!reaped && pidfd >= 0) p[n++] = (struct pollfd){pidfd, POLLIN, 0};
        long wait_ms = 1000; /* the cancel flag is also polled each second */
        if (reaped) wait_ms = quiet_ms ? PTY_QUIET_MS - (now - quiet_ms) : 10000 - (now - reap_ms);
        else if (killed) wait_ms = PTY_KILL_MS + 10000 - (now - hup_ms);
        else if (hup) wait_ms = PTY_KILL_MS - (now - hup_ms);
        if (!reaped && pidfd < 0 && wait_ms > 100) wait_ms = 100;
        if (wait_ms > 1000) wait_ms = 1000;
        if (wait_ms < 0) wait_ms = 0;
        poll(p, (nfds_t)n, (int)wait_ms);
        if (p[wi].revents & POLLIN) {
            uint64_t v;
            (void)!read(s->wake, &v, sizeof(v));
        }
        if (mi >= 0 && want && (p[mi].revents & (POLLIN | POLLHUP | POLLERR))) {
            ssize_t r = read(master, buf, want);
            if (r > 0) {
                slot_put_out(s, buf, (size_t)r);
                quiet_ms = 0;
            } else if (r == 0 || (errno != EAGAIN && errno != EINTR)) {
                eof = 1; /* EIO: no slave fd left */
            }
        }
        if (!reaped && waitpid(pid, status, WNOHANG) == pid) {
            reaped = 1;
            reap_ms = now_mono_ms();
        }
    }
}

static char *pty_ok_frame(const char *id, const char *shell, pid_t pid) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddNumberToObject(o, "v", 1);
    cJSON_AddStringToObject(o, "t", "pty_ok");
    cJSON_AddStringToObject(o, "id", id);
    cJSON_AddItemToObject(o, "shell", json_string_bytes(shell, strlen(shell)));
    cJSON_AddNumberToObject(o, "pid", pid);
    char *s = cJSON_PrintUnformatted(o);
    cJSON_Delete(o);
    return s;
}

static void send_pty_res(const char *id, int reaped, int status) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddNumberToObject(o, "v", 1);
    cJSON_AddStringToObject(o, "t", "res");
    cJSON_AddStringToObject(o, "id", id);
    cJSON_AddBoolToObject(o, "ok", 1);
    if (!reaped) cJSON_AddNullToObject(o, "exit");
    else if (WIFEXITED(status)) cJSON_AddNumberToObject(o, "exit", WEXITSTATUS(status));
    else if (WIFSIGNALED(status)) cJSON_AddNumberToObject(o, "exit", 128 + WTERMSIG(status));
    else cJSON_AddNumberToObject(o, "exit", 1);
    char *s = cJSON_PrintUnformatted(o);
    cJSON_Delete(o);
    if (s) out_push_text(s);
}

static int clamp_dim(int v, int fallback) {
    return v < 1 ? fallback : v > 1000 ? 1000 : v;
}

void pty_session(const char *id, int cols, int rows, const char *cwd, const char *home,
                 const char *path, char *const argv[], char *const envp[], struct job *job) {
    if (!pty_id_ok(id)) {
        send_res(NULL, id, 0, 1, "", "", "invalid terminal id");
        return;
    }
    cols = clamp_dim(cols, 80);
    rows = clamp_dim(rows, 24);
    int wake = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
    if (wake < 0) {
        send_res(NULL, id, 0, 1, "", strerror(errno), "could not open a terminal");
        return;
    }
    struct pty *s = slot_open(id, wake, cols, rows);
    if (!s) {
        close(wake);
        send_res(NULL, id, 0, 1, "", "", "too many terminals open on this machine (16)");
        return;
    }
    char err[PATH_MAX + 64];
    pid_t pid = 0;
    int master = pty_spawn(path, argv, envp, cwd, home, cols, rows, &pid, err, sizeof(err));
    if (master < 0) {
        slot_close(s);
        send_res(NULL, id, 0, 1, "", "", err);
        return;
    }
    int pidfd = open_pidfd(pid);
    char *ok = pty_ok_frame(id, path, pid);
    pthread_mutex_lock(&g_pty_mu);
    s->ok = ok;
    pthread_mutex_unlock(&g_pty_mu);
    out_wake();
    int status = 0;
    int reaped = pty_pump(s, master, pid, pidfd, job, &status);
    close(master); /* hangs up whatever else still runs on the terminal */
    if (pidfd >= 0) close(pidfd);
    /* The res follows the last output byte: give the session (bounded)
     * time to send what is queued. */
    int64_t until = now_mono_ms() + 10000;
    for (;;) {
        pthread_mutex_lock(&g_pty_mu);
        int left = s->ok || s->out_len;
        pthread_mutex_unlock(&g_pty_mu);
        int64_t ms = until - now_mono_ms();
        if (!left || ms <= 0) break;
        wait_wake(wake, ms < 1000 ? ms : 1000);
    }
    slot_close(s);
    send_pty_res(id, reaped, status);
}

void pty_run(const char *id, cJSON *msg, struct job *job) {
    struct passwd pw, *pwp = NULL;
    char pwbuf[4096];
    if (getpwuid_r(geteuid(), &pw, pwbuf, sizeof(pwbuf), &pwp) != 0) pwp = NULL;
    const char *env_home = getenv("HOME");
    const char *home = pwp && pwp->pw_dir && pwp->pw_dir[0] ? pwp->pw_dir : env_home && env_home[0] ? env_home : "/";
    const char *shell = pwp ? pwp->pw_shell : NULL;
    const char *base = shell ? strrchr(shell, '/') : NULL;
    /* A service account's nologin or false would end the terminal at once;
     * the connector already runs commands as this user. */
    if (!base || access(shell, X_OK) != 0 || strcmp(base, "/nologin") == 0 || strcmp(base, "/false") == 0) {
        shell = "/bin/sh";
        base = "/sh";
    }
    char arg0[NAME_MAX + 2];
    snprintf(arg0, sizeof(arg0), "-%s", base + 1); /* a login shell, like sshd starts */
    char cwd[PATH_MAX];
    const char *want = json_cstr(msg, "cwd", "");
    struct stat st;
    if (!want[0] || expand_home(want, cwd, sizeof(cwd)) != 0 || stat(cwd, &st) != 0 || !S_ISDIR(st.st_mode))
        snprintf(cwd, sizeof(cwd), "%s", home);
    char e_home[PATH_MAX + 8], e_shell[PATH_MAX + 8], e_user[300], e_logname[300];
    char *extra[10];
    int k = 0;
    extra[k++] = "TERM=xterm-256color";
    extra[k++] = "COLORTERM=truecolor";
    snprintf(e_home, sizeof(e_home), "HOME=%s", home);
    extra[k++] = e_home;
    snprintf(e_shell, sizeof(e_shell), "SHELL=%s", shell);
    extra[k++] = e_shell;
    if (pwp && pwp->pw_name && pwp->pw_name[0]) {
        snprintf(e_user, sizeof(e_user), "USER=%s", pwp->pw_name);
        snprintf(e_logname, sizeof(e_logname), "LOGNAME=%s", pwp->pw_name);
        extra[k++] = e_user;
        extra[k++] = e_logname;
    }
    const char *lang = getenv("LANG");
    if (!lang || !lang[0]) extra[k++] = "LANG=C.UTF-8";
    const char *path = getenv("PATH");
    if (!path || !path[0]) extra[k++] = "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";
    extra[k] = NULL;
    char **envp = env_with(extra);
    if (!envp) {
        send_res(NULL, id, 0, 1, "", "", "out of memory");
        return;
    }
    char *const argv[] = {arg0, NULL};
    pty_session(id, json_int(msg, "cols", 80), json_int(msg, "rows", 24), cwd, home, shell, argv, envp, job);
    free(envp);
}

void pty_input(const unsigned char *frame, size_t n) {
    char id[PTY_ID_LEN + 1];
    const unsigned char *data;
    size_t dn;
    if (pty_frame_parse(frame, n, id, &data, &dn) != PTY_KIND_IN) return;
    int log = 0;
    pthread_mutex_lock(&g_pty_mu);
    struct pty *s = slot_find(id);
    if (s) {
        if (!pty_buf_put(s->in, &s->in_len, PTY_IN_MAX, data, dn) && !s->in_dropped) log = s->in_dropped = 1;
        slot_wake(s);
    }
    pthread_mutex_unlock(&g_pty_mu);
    if (log) fprintf(stderr, "fuse-agent: terminal %s is not reading its input; dropping input\n", id);
}

void pty_resize(const char *id, int cols, int rows) {
    if (cols < 1 || rows < 1) return;
    pthread_mutex_lock(&g_pty_mu);
    struct pty *s = slot_find(id);
    if (s) {
        s->cols = clamp_dim(cols, 80);
        s->rows = clamp_dim(rows, 24);
        s->resize = 1;
        slot_wake(s);
    }
    pthread_mutex_unlock(&g_pty_mu);
}

void pty_ack(const char *id, double n) {
    if (!(n > 0)) return;
    pthread_mutex_lock(&g_pty_mu);
    struct pty *s = slot_find(id);
    if (s) {
        uint64_t unacked = s->sent - s->acked;
        s->acked += n >= (double)unacked ? unacked : (uint64_t)n;
        slot_wake(s);
    }
    pthread_mutex_unlock(&g_pty_mu);
}

void pty_wake(const char *id) {
    pthread_mutex_lock(&g_pty_mu);
    struct pty *s = slot_find(id);
    if (s) slot_wake(s);
    pthread_mutex_unlock(&g_pty_mu);
}

void pty_reconnected(void) {
    pthread_mutex_lock(&g_pty_mu);
    for (int i = 0; i < PTY_MAX; i++) {
        struct pty *s = &g_ptys[i];
        if (!s->id[0]) continue;
        uint64_t handed = s->sent - s->out_len;
        if (s->acked < handed) s->acked = handed;
        slot_wake(s);
    }
    pthread_mutex_unlock(&g_pty_mu);
}

int pty_pending(void) {
    int pending = 0;
    pthread_mutex_lock(&g_pty_mu);
    for (int i = 0; i < PTY_MAX && !pending; i++)
        pending = g_ptys[i].id[0] && (g_ptys[i].ok || g_ptys[i].out_len);
    pthread_mutex_unlock(&g_pty_mu);
    return pending;
}

int pty_flush_with(int (*send)(void *ctx, int opcode, const unsigned char *data, size_t n), void *ctx) {
    static unsigned char frame[PTY_HDR + PTY_FRAME_MAX]; /* network thread only */
    static int next = 0;
    size_t budget = PTY_FLUSH_BUDGET;
    for (int k = 0; k < PTY_MAX && budget; k++) {
        struct pty *s = &g_ptys[(next + k) % PTY_MAX];
        pthread_mutex_lock(&g_pty_mu);
        unsigned gen = s->gen;
        char *ok = s->id[0] && s->ok ? strdup(s->ok) : NULL;
        size_t n = 0;
        if (s->id[0] && !s->ok && s->out_len) {
            n = s->out_len < PTY_FRAME_MAX ? s->out_len : PTY_FRAME_MAX;
            pty_frame_build(frame, PTY_KIND_OUT, s->id, s->out, n);
        }
        pthread_mutex_unlock(&g_pty_mu);
        if (ok) {
            int rc = send(ctx, 1, (const unsigned char *)ok, strlen(ok));
            free(ok);
            if (rc != 0) return -1;
            pthread_mutex_lock(&g_pty_mu);
            if (s->gen == gen && s->id[0]) {
                free(s->ok);
                s->ok = NULL;
                slot_wake(s);
            }
            pthread_mutex_unlock(&g_pty_mu);
            continue;
        }
        if (!n) continue;
        if (send(ctx, 2, frame, PTY_HDR + n) != 0) return -1;
        pthread_mutex_lock(&g_pty_mu);
        if (s->gen == gen && s->id[0] && s->out_len >= n) {
            memmove(s->out, s->out + n, s->out_len - n);
            s->out_len -= n;
            slot_wake(s);
        }
        pthread_mutex_unlock(&g_pty_mu);
        budget = budget > n ? budget - n : 0;
    }
    next = (next + 1) % PTY_MAX;
    return 0;
}

static int ws_send_ctx(void *ctx, int opcode, const unsigned char *data, size_t n) {
    struct tls *t = ctx;
    return t && t->ssl ? ws_send(t, opcode, data, n) : -1;
}

int pty_flush(struct tls *t) {
    return pty_flush_with(ws_send_ctx, t);
}
