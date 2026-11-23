/* Fuse connector: the http op - a one-shot pinned TLS pipe for the server's
 * web search and page fetch requests. The server resolved and vetted the
 * address and builds and parses the HTTP itself. */
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>

#include <openssl/evp.h>

#include "agent.h"
#include "httpop.h"
#include "net.h"
#include "outq.h"
#include "reconnect.h"

static int clamp(int v, int lo, int hi) {
    return v < lo ? lo : v > hi ? hi : v;
}

/* Standard padded base64 into a malloc'd buffer of 1..max bytes, else NULL.
 * Checked here first: EVP_DecodeBlock skips whitespace, takes '=' anywhere
 * and counts the padding as decoded bytes. */
static unsigned char *unb64(const char *s, size_t max, size_t *n_out) {
    size_t len = strlen(s), pad = 0;
    if (!len || len % 4 || len > 4 * ((max + 2) / 3)) return NULL;
    if (s[len - 1] == '=') pad++;
    if (s[len - 2] == '=') pad++;
    for (size_t i = 0; i < len - pad; i++) {
        char c = s[i];
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '+' || c == '/'))
            return NULL;
    }
    unsigned char *out = malloc(len / 4 * 3);
    if (!out) return NULL;
    int k = EVP_DecodeBlock(out, (const unsigned char *)s, (int)len);
    if (k < 0 || (size_t)k - pad > max) {
        free(out);
        return NULL;
    }
    *n_out = (size_t)k - pad;
    return out;
}

static const char *io_error(int rc, const char *fail) {
    return rc == TLS_TIMEOUT ? "timed out" : rc == TLS_CANCELLED ? "cancelled" : rc == TLS_HANDSHAKE ? "tls failed" : fail;
}

void http_op(struct tls *t, const char *id, cJSON *msg, struct job *job) {
    const char *host = json_cstr(msg, "host", "");
    const char *ip = json_cstr(msg, "ip", "");
    int port = json_int(msg, "port", 443);
    int max_bytes = clamp(json_int(msg, "max_bytes", 1048576), 1, HTTP_MAX_BYTES);
    int64_t deadline = now_mono_ms() + clamp(json_int(msg, "timeout_ms", 15000), 1000, 60000);
    /* inet_pton, not a resolver: only a literal address, never a name. */
    union {
        struct sockaddr sa;
        struct sockaddr_in v4;
        struct sockaddr_in6 v6;
    } addr;
    socklen_t alen = 0;
    memset(&addr, 0, sizeof(addr));
    if (port >= 1 && port <= 65535) {
        if (inet_pton(AF_INET, ip, &addr.v4.sin_addr) == 1) {
            addr.v4.sin_family = AF_INET;
            addr.v4.sin_port = htons((uint16_t)port);
            alen = sizeof(addr.v4);
        } else if (inet_pton(AF_INET6, ip, &addr.v6.sin6_addr) == 1) {
            addr.v6.sin6_family = AF_INET6;
            addr.v6.sin6_port = htons((uint16_t)port);
            alen = sizeof(addr.v6);
        }
    }
    size_t hostlen = strlen(host), req_len = 0;
    unsigned char *req = alen && hostlen >= 1 && hostlen <= 253 ? unb64(json_cstr(msg, "req", ""), HTTP_REQ_MAX, &req_len) : NULL;
    unsigned char *buf = req ? malloc(HTTP_CHUNK) : NULL;
    if (!buf) {
        free(req);
        send_res(t, id, 0, 1, "", "", "invalid args");
        return;
    }
    struct tls c;
    const char *error = NULL, *end = "eof";
    int rc = tls_connect_ip(&c, host, &addr.sa, alen, deadline, job);
    if (rc != 0) error = io_error(rc, "connect failed");
    else if ((rc = tls_write_job(&c, req, req_len, deadline, job)) != 0) error = io_error(rc, "write failed");
    for (int total = 0; !error;) {
        int want = max_bytes - total < HTTP_CHUNK ? max_bytes - total : HTTP_CHUNK;
        rc = tls_read_job(&c, buf, (size_t)want, deadline, job);
        if (rc == TLS_EOF) break;
        if (rc < 0 || send_chunk_b64(id, buf, (size_t)rc) != 0) {
            error = io_error(rc, "read failed");
            break;
        }
        total += rc;
        if (total >= max_bytes) {
            end = "max";
            break;
        }
    }
    tls_close(&c);
    free(buf);
    free(req);
    if (error) send_res(t, id, 0, 1, "", "", error);
    else send_res(t, id, 1, 0, end, "", "");
}
