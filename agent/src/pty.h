#ifndef FUSE_PTY_H
#define FUSE_PTY_H

/* Interactive terminals: a pty_open job runs a login shell on a PTY until it
 * exits. Its bytes travel as binary frames [kind][12-char job id][payload]
 * on their own per-PTY buffers, never the shared job out-queue, so a flood
 * cannot delay results, chunks or pongs. The network thread only touches
 * memory here (never a PTY fd), under g_pty_mu, a leaf lock. */

#include <stdint.h>

#include "agent.h"

#define PTY_MAX 16
#define PTY_ID_LEN 12
#define PTY_HDR (1 + PTY_ID_LEN)
#define PTY_FRAME_MAX 32768 /* payload bytes per binary frame, both ways */
#define PTY_KIND_OUT 0x01
#define PTY_KIND_IN 0x02
/* Output read but not yet acked by the server; the master is left unread
 * (the shell blocks) while this is used up. */
#define PTY_WINDOW (512 * 1024)
/* Output read but not yet sent: two frames, so one can be read while the
 * session sends the other. */
#define PTY_OUT_MAX (2 * PTY_FRAME_MAX)
/* Input waiting for the master to take it. A frame that does not fit whole
 * is dropped: the network thread never waits on a shell that stopped reading. */
#define PTY_IN_MAX (256 * 1024)
/* Output bytes one pty_flush sends, after the job frames, before the session
 * reads the socket again. */
#define PTY_FLUSH_BUDGET (2 * PTY_FRAME_MAX)
#define PTY_KILL_MS 2000 /* SIGHUP on cancel, SIGKILL this long after */
#define PTY_QUIET_MS 100 /* after the shell exits, output this quiet means done */

struct job;
struct tls;

/* Frame helpers. build writes PTY_HDR + n bytes (n <= PTY_FRAME_MAX) and
 * returns that length. parse returns the kind and points data into buf, or
 * -1 for a short frame, a bad id or an oversized payload; id gets the NUL-
 * terminated job id. */
size_t pty_frame_build(unsigned char *out, int kind, const char *id, const unsigned char *data, size_t n);
int pty_frame_parse(const unsigned char *buf, size_t n, char id[PTY_ID_LEN + 1],
                    const unsigned char **data, size_t *dn);
int pty_id_ok(const char *id);
/* Output bytes that may still be read before the window is used up. */
size_t pty_window_left(uint64_t sent, uint64_t acked);
/* Appends data whole when it fits in cap; returns 1, else 0 (nothing taken). */
int pty_buf_put(unsigned char *buf, size_t *len, size_t cap, const unsigned char *data, size_t n);

/* Worker: the pty_open op, from spawn to its res. */
void pty_run(const char *id, cJSON *msg, struct job *job);
/* pty_run once the shell is chosen: exec path with argv and envp in cwd
 * (else home, else /). Sends pty_ok, then the res when the shell exits. */
void pty_session(const char *id, int cols, int rows, const char *cwd, const char *home,
                 const char *path, char *const argv[], char *const envp[], struct job *job);

/* Network thread; none of these block. */
void pty_input(const unsigned char *frame, size_t n);
void pty_resize(const char *id, int cols, int rows);
void pty_ack(const char *id, double n);
void pty_wake(const char *id);
/* A new session: output handed to the old socket is treated as acked, since
 * the server never saw what died in flight and would not ack it. */
void pty_reconnected(void);
int pty_pending(void);
/* Sends pending pty_ok frames and up to PTY_FLUSH_BUDGET output bytes,
 * round-robin. A frame whose send fails stays queued. */
int pty_flush_with(int (*send)(void *ctx, int opcode, const unsigned char *data, size_t n), void *ctx);
int pty_flush(struct tls *t);

#endif
