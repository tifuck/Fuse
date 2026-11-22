/* Forced into every unit by the Makefile (-include).
   With _GNU_SOURCE, glibc 2.38+ headers redirect strtol, strtoll, sscanf and
   friends to __isoc23_* symbols that only exist in glibc 2.38+, so a binary
   built on a new distro fails to load on Debian 12 or Ubuntu 22.04. The agent
   never parses C23 0b literals, so keep the classic symbols. */
#ifndef FUSE_GLIBC_FLOOR_H
#define FUSE_GLIBC_FLOOR_H
#include <features.h>
#undef __GLIBC_USE_C2X_STRTOL
#define __GLIBC_USE_C2X_STRTOL 0
#undef __GLIBC_USE_C23_STRTOL
#define __GLIBC_USE_C23_STRTOL 0
#endif
