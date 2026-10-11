#!/bin/sh
set -e

# TVBackupMatcher Container Entrypoint
# Handles user/group permissions (PUID / PGID) for unRAID, TrueNAS, Synology, and Linux servers.

PUID=${PUID:-1000}
PGID=${PGID:-1000}
UMASK=${UMASK:-002}

umask "$UMASK"

# Create group if it doesn't exist
if ! getent group tvmatcher >/dev/null 2>&1; then
    groupadd -g "$PGID" tvmatcher 2>/dev/null || groupadd tvmatcher
fi

# Create user if it doesn't exist
if ! getent passwd tvmatcher >/dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -d /config -s /bin/sh -M tvmatcher 2>/dev/null || useradd -g "$PGID" -d /config -s /bin/sh -M tvmatcher
fi

# Ensure required persistent directories exist
mkdir -p /config /export /cache
chown -R tvmatcher:tvmatcher /config /export /cache 2>/dev/null || true

echo "================================================================="
echo "   🎬 TVBackupMatcher GUI Container Starting 📺"
echo "================================================================="
echo "  UID: $PUID | GID: $PGID | UMASK: $UMASK"
echo "  Config Directory: /config"
echo "  Media Directory : /media"
echo "  Export Directory: /export"
echo "  Port: ${PORT:-8765}"
echo "================================================================="

# Execute the application as the unprivileged tvmatcher user
exec gosu tvmatcher "$@"
