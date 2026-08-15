#!/usr/bin/env bash
# Encrypted backup of everything that matters: the roster, every agent's
# HERMES_HOME (SOUL.md, memory, transcripts), the shared skills library, and the
# secrets/token files.
#
# Transcripts are business data, so the archive is always encrypted. Uses `age`
# if present, otherwise gpg symmetric.
#
#   ./scripts/backup.sh [dest-dir]        # default: /opt/recons/backups
#   RECIPIENT=age1... ./scripts/backup.sh # encrypt to an age public key
set -euo pipefail

RECONS_ROOT="${RECONS_ROOT:-/opt/recons}"
DEST="${1:-$RECONS_ROOT/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$DEST/recons-$STAMP.tar.gz"

mkdir -p "$DEST"
chmod 700 "$DEST"

echo "== Archiving $RECONS_ROOT =="
tar -C "$RECONS_ROOT" -czf "$ARCHIVE" \
  --exclude='./backups' \
  --exclude='./dashboard' \
  --exclude='./app' \
  . 2>/dev/null

if command -v age >/dev/null && [ -n "${RECIPIENT:-}" ]; then
  echo "== Encrypting with age =="
  age -r "$RECIPIENT" -o "$ARCHIVE.age" "$ARCHIVE"
  rm -f "$ARCHIVE"
  OUT="$ARCHIVE.age"
elif command -v gpg >/dev/null; then
  echo "== Encrypting with gpg (symmetric; you'll be prompted) =="
  gpg --symmetric --cipher-algo AES256 -o "$ARCHIVE.gpg" "$ARCHIVE"
  rm -f "$ARCHIVE"
  OUT="$ARCHIVE.gpg"
else
  echo "!! Neither age nor gpg found — archive left UNENCRYPTED at $ARCHIVE" >&2
  echo "!! Install age (recommended) or gpg, then re-run." >&2
  OUT="$ARCHIVE"
fi

chmod 600 "$OUT"
echo "== Wrote $OUT =="
echo "Restore with: ./scripts/restore.sh $OUT"
echo "Test a restore BEFORE you need one (docs/80-backup-update.md)."
