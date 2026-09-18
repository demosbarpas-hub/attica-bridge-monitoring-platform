#!/usr/bin/env bash
set -euo pipefail

# ====== SETTINGS ======
DB_NAME="database"
MOUNT_ROOT="/media/$USER/gefires_backup"        # external disk mount (with space)
DB_BACKUP_DIR="$MOUNT_ROOT/db_backups"
UPLOADS_SRC="/home/server/Documents/serv/uploads"
UPLOADS_MIRROR_DIR="$MOUNT_ROOT/uploads_mirror"
UPLOADS_ARCHIVES_DIR="$MOUNT_ROOT/uploads_archives"
RETENTION=3
DUMP_FLAGS="--no-tablespaces --single-transaction --quick --default-character-set=utf8mb4"
STAMP=$(date +%Y-%m-%d_%H-%M-%S)
# ======================

log(){ echo "[$(date '+%F %T')] $*"; }

# 0) Run only if the external disk is mounted
if ! mountpoint -q "$MOUNT_ROOT"; then
  log "External disk not mounted ($MOUNT_ROOT). Skipping backup."
  exit 0
fi

# 1) Ensure dirs
mkdir -p "$DB_BACKUP_DIR" "$UPLOADS_MIRROR_DIR" "$UPLOADS_ARCHIVES_DIR"

# 2) DB dump (uses mysql_config_editor login-path set earlier as 'bridgeslp')
DB_OUT="$DB_BACKUP_DIR/${DB_NAME}_$STAMP.sql.gz"
log "Dumping MySQL '$DB_NAME' -> $DB_OUT"
mysqldump --login-path=bridgeslp $DUMP_FLAGS "$DB_NAME" | gzip > "$DB_OUT"
log "DB backup done."

# Keep only last N DB backups
log "Pruning DB backups (keep last $RETENTION)"
cd "$DB_BACKUP_DIR"
ls -1t ${DB_NAME}_*.sql.gz 2>/dev/null | tail -n +$((RETENTION+1)) | xargs -r rm -f --

# 3) UPLOADS mirror (fast restore)
if [[ -d "$UPLOADS_SRC" ]]; then
  log "Syncing uploads mirror: $UPLOADS_SRC -> $UPLOADS_MIRROR_DIR"
  rsync -a --delete "$UPLOADS_SRC/" "$UPLOADS_MIRROR_DIR/"
  log "Uploads mirror sync done."

  # 4) UPLOADS archive (rotating snapshots)
  UP_TAR="$UPLOADS_ARCHIVES_DIR/uploads_$STAMP.tar.gz"
  log "Creating uploads archive -> $UP_TAR"
  tar -czf "$UP_TAR" -C "$(dirname "$UPLOADS_SRC")" "$(basename "$UPLOADS_SRC")"
  log "Uploads archive created."

  log "Pruning upload archives (keep last $RETENTION)"
  cd "$UPLOADS_ARCHIVES_DIR"
  ls -1t uploads_*.tar.gz 2>/dev/null | tail -n +$((RETENTION+1)) | xargs -r rm -f --
else
  log "WARNING: uploads source folder not found: $UPLOADS_SRC (skipping uploads backup)"
fi

log "All done."

