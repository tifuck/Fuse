#ifndef FUSE_EXEC_H
#define FUSE_EXEC_H

/* Child process execution: spawn, capped capture, timeout and cancel kills. */

#include <stddef.h>
#include <sys/types.h>

#include "jobs.h"

int run_exec(const char *cwd, const char *cmd, int timeout_ms, char *out, size_t ocap,
             char *errb, size_t ecap, int *code, struct job *job, int *timed_out, int stream,
             const char *job_id, int *clipped, size_t *out_len, size_t *err_len);

/* run_exec with extra environment for this one child: env is a NULL-ended
 * list of "NAME=value" (or NULL). A name the agent already has is replaced. */
int run_exec_env(const char *cwd, const char *cmd, int timeout_ms, char *out, size_t ocap,
                 char *errb, size_t ecap, int *code, struct job *job, int *timed_out, int stream,
                 const char *job_id, int *clipped, size_t *out_len, size_t *err_len, char *const *env);

/* The agent's environment with extra (NULL-ended "NAME=value") replacing
 * or adding names; the strings are shared, free only the array. NULL when
 * out of memory. */
char **env_with(char *const *extra);
/* Readable once pid exits (Linux 5.3+); -1 means poll waitpid instead. */
int open_pidfd(pid_t pid);

#endif
