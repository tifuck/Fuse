#ifndef FUSE_NET_H
#define FUSE_NET_H

/* Outbound TLS + WebSocket transport: URL parsing, connect, framing. */

#include <sys/socket.h>

#include <openssl/ssl.h>

struct url {
    char host[256];
    char path[256];
    int port;
};
struct tls {
    int fd;
    SSL *ssl;
    char sess_key[272]; /* host:port, for TLS session resumption */
    /* Bytes read past the end of the HTTP handshake header, stashed so the
     * first WebSocket frame is not lost if the server pipelined them. */
    unsigned char pb[512];
    size_t pb_len;
    size_t pb_off;
};

int parse_url(const char *raw, struct url *u);
void url_host_header(const struct url *u, char *out, size_t cap);
int url_join_path(struct url *u, const char *suffix);
int tls_connect(struct tls *t, const struct url *u);
void tls_close(struct tls *t);

/* The http op's pinned one-shot connection, for worker threads: connect to
 * exactly sa (no DNS) while SNI and certificate verification use host. One
 * deadline (now_mono_ms based) spans connect, handshake, write and read; the
 * job's cancel flag is noticed within 250 ms. Never touches the TLS session
 * cache. Negative results: */
enum { TLS_EOF = 0, TLS_FAIL = -1, TLS_TIMEOUT = -2, TLS_CANCELLED = -3, TLS_HANDSHAKE = -4 };
struct job;
int tls_connect_ip(struct tls *t, const char *host, const struct sockaddr *sa, socklen_t len,
                   int64_t deadline, struct job *job);
int tls_write_job(struct tls *t, const void *buf, size_t n, int64_t deadline, struct job *job);
/* Bytes read (> 0), TLS_EOF, or a negative result above. */
int tls_read_job(struct tls *t, void *buf, size_t n, int64_t deadline, struct job *job);
int tls_write_all(struct tls *t, const void *buf, size_t n);
int tls_read_some(struct tls *t, void *buf, size_t n);
int b64(const unsigned char *in, int inlen, char *out, int outcap);
size_t ws_header(int opcode, const unsigned char *mask, size_t n, unsigned char *hdr);
int ws_handshake(struct tls *t, const struct url *u);
int ws_send(struct tls *t, int opcode, const unsigned char *data, size_t n);
int ws_send_text(struct tls *t, const char *s);
char *ws_read_text(struct tls *t, int *opcode_out, size_t *n_out);

#endif
