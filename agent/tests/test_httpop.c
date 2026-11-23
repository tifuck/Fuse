/* The http op against a local TLS server: bad arguments are refused before
 * any connection, the response arrives as ordered base64 chunks ahead of
 * the result, max_bytes and cancel end the read, and a certificate for
 * another name is refused even though the address is pinned. */
#include "../src/agent.h"
#include "../src/httpop.h"
#include "../src/jobs.h"
#include "../src/outq.h"
#include "../src/reconnect.h"
#include <arpa/inet.h>
#include <assert.h>
#include <netinet/in.h>
#include <openssl/evp.h>
#include <openssl/pem.h>
#include <openssl/ssl.h>
#include <openssl/x509.h>
#include <pthread.h>
#include <signal.h>
#include <sys/socket.h>
#include <time.h>

#define BODY 100000

static SSL_CTX *g_sctx;
static int g_listen, g_port, g_timeout_ms = 10000;

static unsigned char body_at(size_t i) {
    return (unsigned char)(i * 7 + (i >> 8)); /* every byte value, NUL included */
}

/* One connection at a time: read the request head, send BODY bytes, then
 * close, or for "GET /hold" wait for the client to go away. */
static void *server(void *unused) {
    (void)unused;
    for (;;) {
        int fd = accept(g_listen, NULL, NULL);
        if (fd < 0) return NULL;
        SSL *s = SSL_new(g_sctx);
        SSL_set_fd(s, fd);
        if (SSL_accept(s) == 1) {
            char req[512];
            size_t got = 0;
            while (got < sizeof(req) - 1) {
                int k = SSL_read(s, req + got, (int)(sizeof(req) - 1 - got));
                if (k <= 0) break;
                got += (size_t)k;
                req[got] = 0;
                if (strstr(req, "\r\n\r\n")) break;
            }
            unsigned char *b = malloc(BODY);
            for (size_t i = 0; i < BODY; i++) b[i] = body_at(i);
            SSL_write(s, b, BODY);
            free(b);
            if (!strncmp(req, "GET /hold", 9)) {
                while (SSL_read(s, req, sizeof(req)) > 0) {
                }
            }
            SSL_shutdown(s);
        }
        SSL_free(s);
        close(fd);
    }
}

struct reply {
    unsigned char *data; /* every chunk, decoded, in queue order */
    size_t n;
    int chunks;
    int ok;
    char out[16], error[32];
};

/* Run one job and take its frames off the queue. */
static struct reply run(const char *id, const char *ip, const char *host, int port, const char *req_b64,
                        int max_bytes, struct job *job) {
    cJSON *msg = cJSON_CreateObject();
    cJSON_AddStringToObject(msg, "id", id);
    if (ip) cJSON_AddStringToObject(msg, "ip", ip);
    if (host) cJSON_AddStringToObject(msg, "host", host);
    cJSON_AddNumberToObject(msg, "port", port);
    if (req_b64) cJSON_AddStringToObject(msg, "req", req_b64);
    if (max_bytes) cJSON_AddNumberToObject(msg, "max_bytes", max_bytes);
    cJSON_AddNumberToObject(msg, "timeout_ms", g_timeout_ms);
    http_op(NULL, id, msg, job);
    cJSON_Delete(msg);

    struct reply r;
    memset(&r, 0, sizeof(r));
    r.data = malloc(BODY + 3);
    assert(g_out_n >= 1);
    while (g_out_n) {
        struct out_frame *f = &g_outq[g_out_head];
        for (size_t i = 0; i < f->n; i++) assert(f->data[i] >= 0x20);
        cJSON *o = cJSON_ParseWithLength((const char *)f->data, f->n);
        assert(o && !strcmp(json_cstr(o, "id", ""), id) && json_int(o, "v", 0) == 1);
        if (g_out_n > 1) {
            /* Chunks first, each at most HTTP_CHUNK raw bytes. */
            const char *d = json_cstr(o, "data", "");
            size_t len = strlen(d), pad = 0;
            assert(!strcmp(json_cstr(o, "t", ""), "chunk") && !strcmp(json_cstr(o, "stream", ""), "b64"));
            assert(len && len % 4 == 0 && len <= 4 * ((HTTP_CHUNK + 2) / 3));
            if (d[len - 1] == '=') pad++;
            if (d[len - 2] == '=') pad++;
            assert(r.n + len / 4 * 3 <= BODY + 3);
            assert(EVP_DecodeBlock(r.data + r.n, (const unsigned char *)d, (int)len) == (int)(len / 4 * 3));
            r.n += len / 4 * 3 - pad;
            r.chunks++;
        } else {
            /* The result is the last frame of the job. */
            assert(!strcmp(json_cstr(o, "t", ""), "res"));
            r.ok = json_bool(o, "ok");
            assert(json_int(o, "exit", -1) == (r.ok ? 0 : 1));
            snprintf(r.out, sizeof(r.out), "%s", json_cstr(o, "out", ""));
            snprintf(r.error, sizeof(r.error), "%s", json_cstr(o, "error", ""));
        }
        cJSON_Delete(o);
        free(f->data);
        g_out_head = (g_out_head + 1) % OUT_QMAX;
        g_out_n--;
    }
    return r;
}

static void refused(const char *ip, const char *host, int port, const char *req_b64) {
    struct reply r = run("bad", ip, host, port, req_b64, 0, NULL);
    assert(!r.ok && r.chunks == 0 && !strcmp(r.error, "invalid args"));
    free(r.data);
}

static void *cancel_soon(void *arg) {
    struct job *job = arg;
    struct timespec d = {0, 300 * 1000000L};
    nanosleep(&d, NULL);
    pthread_mutex_lock(&g_job_mu);
    job->cancel = 1;
    pthread_mutex_unlock(&g_job_mu);
    return NULL;
}

static void body_matches(const struct reply *r, size_t n) {
    assert(r->n == n);
    for (size_t i = 0; i < n; i++) assert(r->data[i] == body_at(i));
}

int main(void) {
    signal(SIGPIPE, SIG_IGN);
    /* A self-signed certificate for fuse.test, trusted through SSL_CERT_FILE
     * (the connector's own trust path), served on a loopback port. */
    EVP_PKEY *key = EVP_EC_gen("P-256");
    X509 *cert = X509_new();
    X509_set_version(cert, 2);
    ASN1_INTEGER_set(X509_get_serialNumber(cert), 1);
    X509_gmtime_adj(X509_getm_notBefore(cert), -60);
    X509_gmtime_adj(X509_getm_notAfter(cert), 3600);
    X509_set_pubkey(cert, key);
    X509_NAME_add_entry_by_txt(X509_get_subject_name(cert), "CN", MBSTRING_ASC, (unsigned char *)"fuse.test", -1, -1, 0);
    X509_set_issuer_name(cert, X509_get_subject_name(cert));
    assert(X509_sign(cert, key, EVP_sha256()));
    char pem[] = "/tmp/fuse-httpop-test-XXXXXX";
    int pfd = mkstemp(pem);
    assert(pfd >= 0);
    FILE *f = fdopen(pfd, "w");
    assert(f && PEM_write_X509(f, cert) == 1);
    fclose(f);
    setenv("SSL_CERT_FILE", pem, 1);
    g_sctx = SSL_CTX_new(TLS_server_method());
    assert(SSL_CTX_use_certificate(g_sctx, cert) == 1 && SSL_CTX_use_PrivateKey(g_sctx, key) == 1);
    g_listen = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in sa = {.sin_family = AF_INET, .sin_addr.s_addr = htonl(INADDR_LOOPBACK)};
    socklen_t slen = sizeof(sa);
    assert(bind(g_listen, (struct sockaddr *)&sa, sizeof(sa)) == 0 && listen(g_listen, 4) == 0);
    assert(getsockname(g_listen, (struct sockaddr *)&sa, &slen) == 0);
    g_port = ntohs(sa.sin_port);
    pthread_t th;
    assert(pthread_create(&th, NULL, server, NULL) == 0);

    /* "GET / HTTP/1.1\r\nConnection: close\r\n\r\n" and the /hold variant. */
    const char *get = "R0VUIC8gSFRUUC8xLjENCkNvbm5lY3Rpb246IGNsb3NlDQoNCg==";
    const char *hold = "R0VUIC9ob2xkIEhUVFAvMS4xDQpDb25uZWN0aW9uOiBjbG9zZQ0KDQo=";

    /* Arguments: only a literal address, a sane host and port, and strict
     * base64 of 1..HTTP_REQ_MAX bytes. */
    refused("localhost", "fuse.test", g_port, get);
    refused("127.1", "fuse.test", g_port, get);
    refused("127.0.0.1 ", "fuse.test", g_port, get);
    refused(NULL, "fuse.test", g_port, get);
    refused("127.0.0.1", NULL, g_port, get);
    refused("127.0.0.1", "fuse.test", 0, get);
    refused("127.0.0.1", "fuse.test", 65536, get);
    refused("127.0.0.1", "fuse.test", g_port, NULL);
    refused("127.0.0.1", "fuse.test", g_port, "");
    refused("127.0.0.1", "fuse.test", g_port, "R0VU IC8=");
    refused("127.0.0.1", "fuse.test", g_port, "R0=UIC8=");
    refused("127.0.0.1", "fuse.test", g_port, "R0VUIC8");
    char longhost[256];
    memset(longhost, 'a', 254);
    longhost[254] = 0;
    refused("127.0.0.1", longhost, g_port, get);
    size_t big = 4 * ((HTTP_REQ_MAX + 1 + 2) / 3);
    char *over = malloc(big + 1);
    memset(over, 'A', big); /* HTTP_REQ_MAX + 2 bytes */
    over[big] = 0;
    refused("127.0.0.1", "fuse.test", g_port, over);
    memcpy(over + big - 4, "AAA=", 4); /* HTTP_REQ_MAX + 1 bytes: just over */
    refused("127.0.0.1", "fuse.test", g_port, over);
    free(over);

    /* The job dispatcher knows the op. */
    cJSON *job = cJSON_Parse("{\"v\":1,\"t\":\"job\",\"id\":\"viajob\",\"op\":\"http\",\"host\":\"fuse.test\",\"ip\":\"fuse.test\"}");
    handle_job(NULL, job);
    cJSON_Delete(job);
    assert(g_out_n == 1 && memmem(g_outq[g_out_head].data, g_outq[g_out_head].n, "invalid args", 12));
    free(g_outq[g_out_head].data);
    g_out_head = (g_out_head + 1) % OUT_QMAX;
    g_out_n--;

    /* The whole body, in order, across several chunks, then "eof". */
    struct reply r = run("eof", "127.0.0.1", "fuse.test", g_port, get, 0, NULL);
    assert(r.ok && !strcmp(r.out, "eof") && !r.error[0]);
    assert(r.chunks > 1);
    body_matches(&r, BODY);
    free(r.data);

    /* max_bytes stops the read exactly there. */
    r = run("max", "127.0.0.1", "fuse.test", g_port, get, 50000, NULL);
    assert(r.ok && !strcmp(r.out, "max"));
    body_matches(&r, 50000);
    free(r.data);

    /* The pinned address does not replace the hostname check. */
    r = run("name", "127.0.0.1", "other.test", g_port, get, 0, NULL);
    assert(!r.ok && r.chunks == 0 && !strcmp(r.error, "tls failed"));
    free(r.data);

    /* A cancel ends a read the server never closes; relayed bytes stay. */
    assert(job_admit("cancel"));
    pthread_t canceller;
    assert(pthread_create(&canceller, NULL, cancel_soon, job_reserve("cancel")) == 0);
    int64_t t0 = now_mono_ms();
    r = run("cancel", "127.0.0.1", "fuse.test", g_port, hold, 0, job_reserve("cancel"));
    assert(!r.ok && !strcmp(r.error, "cancelled"));
    assert(now_mono_ms() - t0 < 2000);
    body_matches(&r, BODY);
    free(r.data);
    pthread_join(canceller, NULL);

    /* So does the deadline (clamped up to one second). */
    g_timeout_ms = 1;
    t0 = now_mono_ms();
    r = run("slow", "127.0.0.1", "fuse.test", g_port, hold, 0, NULL);
    assert(!r.ok && !strcmp(r.error, "timed out"));
    assert(now_mono_ms() - t0 >= 900 && now_mono_ms() - t0 < 2000);
    body_matches(&r, BODY);
    free(r.data);
    g_timeout_ms = 10000;

    shutdown(g_listen, SHUT_RDWR);
    close(g_listen);
    pthread_join(th, NULL);
    r = run("gone", "127.0.0.1", "fuse.test", g_port, get, 0, NULL);
    assert(!r.ok && r.chunks == 0 && !strcmp(r.error, "connect failed"));
    free(r.data);
    unlink(pem);
    puts("test_httpop: OK");
    return 0;
}
