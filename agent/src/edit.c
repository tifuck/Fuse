/* Fuse connector: write-side filesystem ops (atomic writes, exact edits). */
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>

#include "agent.h"
#include "edit.h"
#include "files.h"

/* The process umask without the umask(0)/umask(old) race (other threads may
 * be creating files): Linux reports it in /proc/self/status. */
static mode_t current_umask(void) {
    unsigned int um = 022;
    FILE *f = fopen("/proc/self/status", "r");
    if (f) {
        char line[256];
        while (fgets(line, sizeof(line), f)) {
            if (sscanf(line, "Umask:\t%o", &um) == 1) break;
        }
        fclose(f);
    }
    return (mode_t)(um & 0777);
}

static int write_all_fd(int fd, const char *content, size_t n) {
    size_t done = 0;
    while (done < n) {
        ssize_t wrote = write(fd, content + done, n - done);
        if (wrote < 0 && errno == EINTR) continue;
        if (wrote <= 0) return -1;
        done += (size_t)wrote;
    }
    return 0;
}

/* The whole file, or NULL (unreadable, or no memory): a restore copy only. */
static char *read_all_fd(int fd, size_t *len) {
    struct stat st;
    if (fstat(fd, &st) != 0 || st.st_size < 0) return NULL;
    size_t want = (size_t)st.st_size, got = 0;
    char *buf = malloc(want ? want : 1);
    if (!buf) return NULL;
    while (got < want) {
        ssize_t r = read(fd, buf + got, want - got);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) {
            free(buf);
            return NULL;
        }
        got += (size_t)r;
    }
    *len = got;
    return buf;
}

/* Rewrite an existing regular file in place: same inode, so hard links,
 * owner, ACLs and xattrs are untouched. Not atomic; used only when a sibling
 * temp file would break a hard link or cannot be created at all. The new
 * bytes overwrite the old ones before the file is cut to length, and the old
 * bytes are kept so a failed write (ENOSPC, EIO) puts them back instead of
 * leaving the file truncated. A readable file whose copy cannot be taken is
 * not rewritten. */
static int write_in_place(const char *path, const char *content, size_t n) {
    char *orig = NULL;
    size_t olen = 0;
    int fd = open(path, O_RDWR | O_NOFOLLOW | O_CLOEXEC);
    if (fd >= 0) {
        errno = 0;
        orig = read_all_fd(fd, &olen);
        if (!orig) {
            int e = errno ? errno : EIO;
            close(fd);
            errno = e;
            return -1;
        }
    } else {
        fd = open(path, O_WRONLY | O_NOFOLLOW | O_CLOEXEC); /* write-only: no copy */
    }
    if (fd < 0) return -1;
    if (lseek(fd, 0, SEEK_SET) < 0) {
        int e = errno;
        free(orig);
        close(fd);
        errno = e;
        return -1;
    }
    if (write_all_fd(fd, content, n) != 0 || ftruncate(fd, (off_t)n) != 0 || fsync(fd) != 0) {
        int e = errno;
        int restored = orig && lseek(fd, 0, SEEK_SET) == 0 && write_all_fd(fd, orig, olen) == 0 &&
                       ftruncate(fd, (off_t)olen) == 0 && fsync(fd) == 0;
        free(orig);
        close(fd);
        errno = e;
        /* -2: the old bytes are not back (no copy, or the restore failed), so
         * the file may hold part of each; the caller must say so. */
        return restored ? -1 : -2;
    }
    free(orig);
    return close(fd);
}

/* mkdir -p for a new file's parent; the umask applies as for any mkdir. */
static int mkdir_parents(const char *dir) {
    char buf[PATH_MAX];
    size_t n = strlen(dir);
    if (n == 0 || n >= sizeof(buf)) return -1;
    memcpy(buf, dir, n + 1);
    for (size_t i = 1; i <= n; i++) {
        if (buf[i] != '/' && buf[i] != 0) continue;
        char saved = buf[i];
        buf[i] = 0;
        if (mkdir(buf, 0777) != 0 && errno != EEXIST) return -1;
        buf[i] = saved;
    }
    return 0;
}

int write_file_atomic_limit(const char *path, const char *content, long max) {
    if (content && (long)strlen(content) > max) { errno = EFBIG; return -1; }
    char expanded[PATH_MAX];
    if (expand_home(path, expanded, sizeof(expanded)) != 0) return -1;
    path = expanded;
    char tmp[PATH_MAX];
    char dir[PATH_MAX];
    const char *slash = strrchr(path, '/');
    if (slash) {
        size_t n = (size_t)(slash - path);
        if (n >= sizeof(dir)) return -1;
        memcpy(dir, path, n);
        dir[n] = 0;
        if (n == 0) strcpy(dir, "/");
    } else {
        strcpy(dir, ".");
    }
    size_t n = content ? strlen(content) : 0;
    struct stat st;
    int exists = 0;
    /* New files get what any editor would create (0666 minus the umask), not
     * mkstemp's 0600 that other service users cannot read. */
    mode_t mode = 0666 & ~current_umask();
    if (lstat(path, &st) == 0) {
        if (!S_ISREG(st.st_mode)) {
            errno = EINVAL;
            return -1;
        }
        exists = 1;
        mode = st.st_mode & 0777;
        /* A rename would split a hard-linked file into two copies. */
        if (st.st_nlink > 1) return write_in_place(path, content ? content : "", n);
    } else if (errno != ENOENT) {
        return -1;
    } else if (mkdir_parents(dir) != 0) {
        return -1;
    }
    if (snprintf(tmp, sizeof(tmp), "%s/.fuse-XXXXXX", dir) >= (int)sizeof(tmp)) return -1;
    int fd = mkostemp(tmp, O_CLOEXEC);
    if (fd < 0) {
        /* Directory not writable but the file is: edit the file itself. */
        if (exists && (errno == EACCES || errno == EPERM))
            return write_in_place(path, content ? content : "", n);
        return -1;
    }
    if (fchmod(fd, mode) != 0) {
        close(fd);
        unlink(tmp);
        return -1;
    }
    /* Keep the original owner where permitted (e.g. root editing a user's
     * file); an unprivileged connector cannot, and that is not an error. */
    if (exists && (st.st_uid != geteuid() || st.st_gid != getegid()))
        if (fchown(fd, st.st_uid, st.st_gid) != 0) { /* not permitted: keep ours */ }
    if (write_all_fd(fd, content ? content : "", n) != 0 || fsync(fd) != 0) {
        close(fd);
        unlink(tmp);
        return -1;
    }
    if (close(fd) != 0) {
        unlink(tmp);
        return -1;
    }
    if (rename(tmp, path) != 0) {
        unlink(tmp);
        return -1;
    }
    /* Make the rename itself durable: fsync the parent directory. */
    int dfd = open(dir, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (dfd >= 0) {
        (void)fsync(dfd);
        close(dfd);
    }
    return 0;
}

int write_file_atomic(const char *path, const char *content) {
    return write_file_atomic_limit(path, content, WRITE_FILE_MAX);
}

int load_whole(const char *path, char **buf, size_t *len) {
    char expanded[PATH_MAX];
    if (expand_home(path, expanded, sizeof(expanded)) != 0) return -1;
    path = expanded;
    /* Non-blocking open + regular-file check: a FIFO must not hang a worker. */
    int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return -1;
    struct stat st;
    if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode)) {
        close(fd);
        errno = EINVAL;
        return -1;
    }
    FILE *f = fdopen(fd, "rb");
    if (!f) {
        close(fd);
        return -1;
    }
    if (fseek(f, 0, SEEK_END) != 0) {
        fclose(f);
        return -1;
    }
    long sz = ftell(f);
    if (sz < 0 || sz > EDIT_FILE_MAX) {
        fclose(f);
        /* Callers translate errno into the actual cause; a bare failure
         * sends the model into a blind retry loop. */
        errno = sz > 0 ? EFBIG : EIO;
        return -1;
    }
    rewind(f);
    char *p = malloc((size_t)sz + 1);
    if (!p) {
        fclose(f);
        return -1;
    }
    if (sz && fread(p, 1, (size_t)sz, f) != (size_t)sz) {
        free(p);
        fclose(f);
        return -1;
    }
    p[sz] = 0;
    if (memchr(p, 0, (size_t)sz) || fgetc(f) != EOF || ferror(f)) {
        free(p);
        fclose(f);
        errno = EINVAL;
        return -1;
    }
    fclose(f);
    *buf = p;
    *len = (size_t)sz;
    return 0;
}

/* Expand LF to CRLF in a NUL-terminated string (edit-argument repair for
 * CRLF files; read_file output hides carriage returns). NULL on OOM. */
char *crlf_expand(const char *s, size_t n) {
    size_t nl = 0;
    for (size_t i = 0; i < n; i++)
        if (s[i] == '\n') nl++;
    char *out = malloc(n + nl + 1);
    if (!out) return NULL;
    char *w = out;
    for (size_t i = 0; i < n; i++) {
        if (s[i] == '\n' && (i == 0 || s[i - 1] != '\r')) *w++ = '\r';
        *w++ = s[i];
    }
    *w = 0;
    return out;
}

/* Length of old matched at p, each '\n' in old taking LF or CRLF; 0 if none. */
static size_t eol_match(const char *p, const char *end, const char *old) {
    const char *s = p;
    for (; *old; old++) {
        if (*old == '\n' && end - s >= 2 && s[0] == '\r' && s[1] == '\n') s += 2;
        else if (s < end && *s == *old) s++;
        else return 0;
    }
    return (size_t)(s - p);
}

/* Next match at or after p (length in *m), or NULL. k = old's first-line length. */
static const char *next_match(const char *p, const char *end, const char *old, size_t k, size_t *m) {
    while (p < end) {
        const char *c = k ? memmem(p, (size_t)(end - p), old, k) : p + strcspn(p, "\r\n");
        if (!c || c >= end) return NULL;
        if ((*m = eol_match(c, end, old))) return c;
        p = c + 1;
    }
    return NULL;
}

/* Whether a match ends its lines with CRLF: its own ending when it spans
 * lines, else that of the line it sits on (CRLF on a final line). */
static int match_crlf(const char *hit, size_t m, const char *end) {
    if (memchr(hit, '\n', m)) return memmem(hit, m, "\r\n", 2) != NULL;
    const char *nl = memchr(hit + m, '\n', (size_t)(end - hit - m));
    return !nl || nl[-1] == '\r';
}

/* Apply one exact replacement to a NUL-terminated heap buffer, replacing it
 * on success. LF-only old/new against a text with CRLF (or mixed) endings
 * match either ending, and new takes the matched region's (read_file hides
 * carriage returns) — deterministic, never fuzzy. */
int apply_edit(char **bufp, size_t *lenp, const char *old, const char *newv, int all,
                      long *reps_out, char *why, size_t whylen) {
    char *buf = *bufp;
    size_t len = *lenp;
    if (!old || !old[0]) {
        snprintf(why, whylen, "old_string required");
        return -1;
    }
    if (strcmp(old, newv) == 0) {
        snprintf(why, whylen, "old_string and new_string are identical");
        return -1;
    }
    const char *end = buf + len;
    size_t oldn = strlen(old), newn = strlen(newv);
    int eol = memmem(buf, len, "\r\n", 2) && !strchr(old, '\r') && !strchr(newv, '\r');
    size_t k = eol ? strcspn(old, "\n") : oldn;
    char *new_crlf = eol ? crlf_expand(newv, newn) : NULL;
    if (eol && !new_crlf) {
        snprintf(why, whylen, "out of memory");
        return -1;
    }
    size_t crlfn = new_crlf ? strlen(new_crlf) : newn;
    long count = 0, final = (long)len;
    size_t m = oldn;
    for (const char *p = buf; (p = eol ? next_match(p, end, old, k, &m) : strstr(p, old)); p += m) {
        count++;
        if (count == 1 || all) final += (long)(eol && match_crlf(p, m, end) ? crlfn : newn) - (long)m;
    }
    if (count == 0) {
        free(new_crlf);
        snprintf(why, whylen, "old_string not found");
        return -1;
    }
    if (count > 1 && !all) {
        free(new_crlf);
        snprintf(why, whylen, "old_string is not unique (%ld occurrences)", count);
        return -1;
    }
    long reps = all ? count : 1;
    if (final > EDIT_FILE_MAX) {
        free(new_crlf);
        snprintf(why, whylen,
                 "replacement too large (maximum %ld bytes); apply the change with run_terminal_command",
                 EDIT_FILE_MAX);
        return -1;
    }
    char *nb = malloc((size_t)final + 1);
    if (!nb) {
        free(new_crlf);
        snprintf(why, whylen, "out of memory");
        return -1;
    }
    char *w = nb;
    const char *r = buf;
    for (long left = reps; left; left--) {
        const char *hit = eol ? next_match(r, end, old, k, &m) : strstr(r, old);
        if (!hit) break;
        int crlf = eol && match_crlf(hit, m, end);
        size_t pre = (size_t)(hit - r);
        memcpy(w, r, pre);
        w += pre;
        memcpy(w, crlf ? new_crlf : newv, crlf ? crlfn : newn);
        w += crlf ? crlfn : newn;
        r = hit + m;
    }
    strcpy(w, r);
    free(new_crlf);
    free(buf);
    *bufp = nb;
    *lenp = strlen(nb);
    *reps_out = reps;
    return 0;
}
