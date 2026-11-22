#!/bin/sh
command -v vox >/dev/null 2>&1 || exit 0
exec vox hook context-event --enabled-only
