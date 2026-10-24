#!/usr/bin/env bash
# A stand-in for `docker`, for testing docker/linux/alertbot (config/tests_linux_scripts.py). Every
# call is appended to $FAKE_DOCKER_LOG. What it answers is set with environment variables:
#   FAIL_ON            fail any call that contains this text
#   FAKE_INFO          what `docker info` says: linux (default), denied, down
#   FAKE_COMPOSE_VERSION  (2.29.1)
#   FAKE_NOT_SET_UP    no data volume yet
#   FAKE_RUNNING       the bot's containers run (1) or not (empty)
#   FAKE_OTHER_VERSION the running containers use another image than docker/compose.env's
#   FAKE_HEALTH        what /api/health/ answers (default: 200 and FAKE_VERSION); FAKE_BROKEN_IMAGE
#   FAKE_VERSION       the version the running bot reports (1.0.0-rc.4)
#   FAKE_DATA_USERS    containers that use the data volume; FAKE_PS_FAIL: Docker doesn't answer
#   FAKE_COMPAT        check_compat's exit code
#   FAKE_VOLUME_BACKUPS  backup files in the volume (space separated)
#   FAKE_REPORT        restore --report exit code (0, 10, 3); FAKE_RESTORE_RUN: restore --expect's
#   FAKE_PLAN          update-plan: update, up_to_date, blocked; FAKE_STEPS, FAKE_FINAL
#   FAKE_TARGET_VERSION  what `describe` says (1.0.0-rc.4)
#   FAKE_PREFLIGHT, FAKE_TRIAL, FAKE_RESTORE  exit codes of those steps
#   FAKE_FP_LATER      the fingerprint after the first one
#   FAKE_LOCK_HELD     the update lock exists; FAKE_BUNDLE_DIR: where `docker cp` copies from
#   FAKE_IMAGE_MISSING images whose name contains this text aren't here; FAKE_PULL_FAIL
#   FAKE_UP_FAIL       `compose up` fails
log="${FAKE_DOCKER_LOG:?}"
printf '%s\n' "$*" >> "$log"
all="$*"
if [ -n "${FAIL_ON:-}" ] && [[ "$all" == *"$FAIL_ON"* ]]; then echo "fake docker: failing on purpose" >&2; exit 1; fi
version="${FAKE_VERSION:-1.0.0-rc.4}"

compose() {
  shift  # compose
  while [ $# -gt 0 ]; do case "$1" in -p|--env-file|-f) shift 2 ;; *) break ;; esac; done
  case "$1" in
    version) echo "${FAKE_COMPOSE_VERSION:-2.29.1}" ;;
    ps)
      if [[ "$all" == *"--format"* ]]; then printf 'SERVICE STATE STATUS\nweb running Up\n'; exit 0; fi
      [ -n "${FAKE_RUNNING:-}" ] || exit 0
      if [[ "$all" == *" web"* ]]; then echo "cid-web"; else printf 'cid-web\ncid-checker\n'; fi ;;
    up) [ -n "${FAKE_UP_FAIL:-}" ] && { echo "fake docker: port is already allocated" >&2; exit 1; }; exit 0 ;;
    stop|start) exit 0 ;;
    logs) echo "fake logs" ;;
    exec) healthy="200 {\"status\":\"ok\",\"version\":\"$version\"}"
          # FAKE_BROKEN_IMAGE: the bot doesn't answer when docker/compose.env pins an image containing this
          if [ -n "${FAKE_BROKEN_IMAGE:-}" ] && grep -q "^ALERTBOT_IMAGE=.*$FAKE_BROKEN_IMAGE" docker/compose.env; then
            echo "503 {}"; exit 0
          fi
          echo "${FAKE_HEALTH:-$healthy}" ;;
    run)
      case "$all" in
        *check_compat*) exit "${FAKE_COMPAT:-0}" ;;
        *" backup --label "*)
          label="${all##* --label }"; label="${label%% *}"
          echo "Database backed up to /data/backups/db-$label-20261024-000000-000001.sqlite3 (checked: complete and undamaged)" ;;
        *"restore --report"*)
          echo "Restoring this backup would undo: nothing that counts."
          [ "${FAKE_REPORT:-0}" = 10 ] && echo "IMPACT=lost-1" || echo "IMPACT=none"
          [ "${FAKE_REPORT:-0}" = 3 ] && { echo "IMPACT="; exit 3; }
          exit "${FAKE_REPORT:-0}" ;;
        *"restore --expect"*) exit "${FAKE_RESTORE_RUN:-0}" ;;
      esac ;;
  esac
  exit 0
}

case "$1" in
  info)
    case "${FAKE_INFO:-linux}" in
      linux) echo linux ;;
      denied) echo "permission denied while trying to connect to the Docker daemon socket" >&2; exit 1 ;;
      *) echo "Cannot connect to the Docker daemon. Is the docker daemon running?" >&2; exit 1 ;;
    esac ;;
  compose) compose "$@" ;;
  volume) [ -n "${FAKE_NOT_SET_UP:-}" ] && exit 1; exit 0 ;;
  image)
    if [ -n "${FAKE_IMAGE_MISSING:-}" ] && [[ "$all" == *"$FAKE_IMAGE_MISSING"* ]]; then exit 1; fi
    [[ "$all" == *"--format"* ]] && echo "sha256:same"
    exit 0 ;;
  inspect) [ -n "${FAKE_OTHER_VERSION:-}" ] && echo "sha256:other" || echo "sha256:same" ;;
  pull) [ -n "${FAKE_PULL_FAIL:-}" ] && { echo "fake docker: manifest unknown" >&2; exit 1; }; exit 0 ;;
  ps)
    [ -n "${FAKE_PS_FAIL:-}" ] && exit 1
    for name in ${FAKE_DATA_USERS:-}; do echo "$name"; done ;;
  create)
    [[ "$all" == *"-update-lock"* ]] && [ -n "${FAKE_LOCK_HELD:-}" ] && exit 1
    [[ "$all" == *"-update-lock"* ]] && [ -n "${FAKE_LOCK_HELD_ONCE:-}" ] && [ ! -e "$log.lock-taken" ] && { : > "$log.lock-taken"; exit 1; }
    echo "cid-created" ;;
  rm) exit 0 ;;
  cp)
    [ -n "${FAKE_CP_FAIL:-}" ] && exit 1
    case "$2" in
      *:/app/bundle/linux) cp -r "${FAKE_BUNDLE_DIR:?}/linux" "$3" ;;
      *:/app/bundle/linux.sha256) cp "${FAKE_BUNDLE_DIR:?}/linux.sha256" "$3" ;;
      *) exit 1 ;;
    esac ;;
  run)
    case "$all" in
      *" update-plan "*)
        case "${FAKE_PLAN:-}" in
          update)
            echo "RESULT=update"; echo "REASON=update"; echo "CURRENT_VERSION=1.0.0-rc.3"
            echo "MESSAGE=1.0.0-rc.4 is available (you have 1.0.0-rc.3)."
            echo "TARGET_VERSION=1.0.0-rc.4"
            echo "TARGET_IMAGE=ghcr.io/qazwsx08ghj/bluesky-discord-alert-bot:1.0.0-rc.4@sha256:$(printf 'b%.0s' {1..64})"
            echo "FINAL_VERSION=${FAKE_FINAL:-1.0.0-rc.4}"; echo "STEPS=${FAKE_STEPS:-1}"
            echo "SEEN_REVISION=2"; echo "SEEN_DIGEST=$(printf 'c%.0s' {1..64})" ;;
          up_to_date)
            echo "RESULT=up_to_date"; echo "REASON=up_to_date"; echo "CURRENT_VERSION=1.0.0-rc.3"
            echo "MESSAGE=You have the newest version (1.0.0-rc.3)."
            echo "SEEN_REVISION=2"; echo "SEEN_DIGEST=$(printf 'c%.0s' {1..64})" ;;
          blocked)
            echo "RESULT=blocked"; echo "REASON=metadata_unavailable"; echo "CURRENT_VERSION=1.0.0-rc.3"
            echo "MESSAGE=Couldn't check for updates: GitHub couldn't be reached (URLError). Nothing was changed." ;;
        esac ;;
      *" describe"*) printf '{\n  "version": "%s"\n}\n' "${FAKE_TARGET_VERSION:-1.0.0-rc.4}" ;;
      *" update-preflight"*) [ -n "${FAKE_PREFLIGHT:-}" ] && echo "fake docker: preflight failed"; exit "${FAKE_PREFLIGHT:-0}" ;;
      *" trial"*) [ -n "${FAKE_TRIAL:-}" ] && echo "fake docker: a migration failed"; exit "${FAKE_TRIAL:-0}" ;;
      *" fingerprint"*)
        if [ -e "$log.fp" ]; then echo "FINGERPRINT=${FAKE_FP_LATER:-aaaaaaaaaaaaaaaaaaaa}"
        else : > "$log.fp"; echo "FINGERPRINT=aaaaaaaaaaaaaaaaaaaa"; fi ;;
      *" restore /data/backups/"*) exit "${FAKE_RESTORE:-0}" ;;
      *"--entrypoint cat "*) name="${all##*/data/backups/}"; printf 'backup-bytes-%s' "$name" ;;
      *"--entrypoint rm "*) exit 0 ;;
      *"--entrypoint sh "*)
        case "$all" in
          *"ls -1"*) for name in ${FAKE_VOLUME_BACKUPS:-}; do echo "$name"; done ;;
          *"cat > "*) cat > "$log.staged"; sha256sum < "$log.staged" | cut -d' ' -f1 ;;
          *"stat -c"*) name="${all##*/data/backups/}"
                       printf 'backup-bytes-%s' "$name" | sha256sum | cut -d' ' -f1; echo 1793000000 ;;
        esac ;;
    esac ;;
esac
exit 0
