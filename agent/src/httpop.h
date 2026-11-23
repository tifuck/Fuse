#ifndef FUSE_HTTPOP_H
#define FUSE_HTTPOP_H

/* The http op: one pinned TLS connection that writes the server's request
 * bytes and relays the raw response as base64 chunks. Never parses HTTP. */

#include "agent.h"
#include "jobs.h"

#define HTTP_REQ_MAX 65536
#define HTTP_CHUNK 32768
#define HTTP_MAX_BYTES (32 * 1024 * 1024)

struct tls;

void http_op(struct tls *t, const char *id, cJSON *msg, struct job *job);

#endif
