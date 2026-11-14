#!/bin/bash
chmod 4755 "/opt/Fuse/chrome-sandbox" 2>/dev/null ||
  echo "fuse-desktop: could not make /opt/Fuse/chrome-sandbox setuid; where user namespaces are restricted Fuse runs without the Chromium sandbox" >&2
