#!/usr/bin/env bash
#
# Checks CPU, memory, disk, processes and zombies. Alerts go to stderr and the
# log file. Thresholds are environment variables, see --help.
#
# Exit status: 0 healthy, 1 alert, 2 bad configuration.

set -uo pipefail

CPU_THRESHOLD=${CPU_THRESHOLD:-80}
MEM_THRESHOLD=${MEM_THRESHOLD:-80}
DISK_THRESHOLD=${DISK_THRESHOLD:-85}
PROCESS_THRESHOLD=${PROCESS_THRESHOLD:-400}
ZOMBIE_THRESHOLD=${ZOMBIE_THRESHOLD:-5}
SAMPLE_SECONDS=${SAMPLE_SECONDS:-1}
DISK_PATH=${DISK_PATH:-/}
LOG_FILE=${LOG_FILE:-./system-health.log}
REQUIRED_PROCESSES=${REQUIRED_PROCESSES:-}

alerts=0
cpu_usage=0
mem_usage=0
disk_usage=0
proc_count=0
zombie_count=0

usage() {
    cat <<'EOF'
Usage: system_health_monitor.sh [-h]

Environment:
  CPU_THRESHOLD       percent, default 80
  MEM_THRESHOLD       percent, default 80
  DISK_THRESHOLD      percent, default 85
  PROCESS_THRESHOLD   total process count, default 400
  ZOMBIE_THRESHOLD    zombie process count, default 5
  SAMPLE_SECONDS      CPU sample window, default 1
  DISK_PATH           filesystem to check, default /
  LOG_FILE            default ./system-health.log
  REQUIRED_PROCESSES  comma separated names that must be running
EOF
}

alert() {
    alerts=$((alerts + 1))
    printf '%s ALERT %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG_FILE" >&2
}

is_uint() {
    [[ $1 =~ ^[0-9]+$ ]]
}

validate_config() {
    local name value
    for name in CPU_THRESHOLD MEM_THRESHOLD DISK_THRESHOLD PROCESS_THRESHOLD ZOMBIE_THRESHOLD SAMPLE_SECONDS; do
        value=${!name}
        if ! is_uint "$value"; then
            echo "$name must be a whole number, got '$value'" >&2
            exit 2
        fi
    done

    if (( SAMPLE_SECONDS < 1 )); then
        echo "SAMPLE_SECONDS must be at least 1" >&2
        exit 2
    fi

    for name in CPU_THRESHOLD MEM_THRESHOLD DISK_THRESHOLD; do
        value=${!name}
        if (( value > 100 )); then
            echo "$name cannot be above 100" >&2
            exit 2
        fi
    done

    if [[ ! -r /proc/stat || ! -r /proc/meminfo ]]; then
        echo "this script needs /proc, so it only runs on Linux" >&2
        exit 2
    fi

    if ! mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null; then
        echo "cannot create the log directory for $LOG_FILE" >&2
        exit 2
    fi

    if ! : >>"$LOG_FILE" 2>/dev/null; then
        echo "cannot write to $LOG_FILE" >&2
        exit 2
    fi
}

cpu_sample() {
    local user nice system idle iowait irq softirq steal
    read -r _ user nice system idle iowait irq softirq steal _ </proc/stat
    local idle_ticks=$((idle + iowait))
    local total_ticks=$((user + nice + system + idle + iowait + irq + softirq + steal))
    printf '%s %s\n' "$total_ticks" "$idle_ticks"
}

check_cpu() {
    local total1 idle1 total2 idle2 total_delta idle_delta

    read -r total1 idle1 <<<"$(cpu_sample)"
    sleep "$SAMPLE_SECONDS"
    read -r total2 idle2 <<<"$(cpu_sample)"

    total_delta=$((total2 - total1))
    idle_delta=$((idle2 - idle1))

    if (( total_delta <= 0 )); then
        cpu_usage=0
    else
        cpu_usage=$(( 100 * (total_delta - idle_delta) / total_delta ))
    fi

    if (( cpu_usage > CPU_THRESHOLD )); then
        alert "cpu usage ${cpu_usage}% is above ${CPU_THRESHOLD}%"
    fi
}

check_memory() {
    read -r mem_usage <<<"$(awk '
        /^MemTotal:/ { total = $2 }
        /^MemAvailable:/ { available = $2 }
        END {
            if (total > 0) printf "%d", 100 * (total - available) / total
            else print 0
        }' /proc/meminfo)"

    if (( mem_usage > MEM_THRESHOLD )); then
        alert "memory usage ${mem_usage}% is above ${MEM_THRESHOLD}%"
    fi
}

check_disk() {
    local used

    used=$(df -P "$DISK_PATH" 2>/dev/null | awk 'NR == 2 { sub("%", "", $5); print $5 }')

    if [[ -z $used ]]; then
        alert "could not read disk usage for ${DISK_PATH}"
        return
    fi

    disk_usage=$used

    if (( disk_usage > DISK_THRESHOLD )); then
        alert "disk usage ${disk_usage}% on ${DISK_PATH} is above ${DISK_THRESHOLD}%"
    fi
}

check_processes() {
    proc_count=$(ps -e --no-headers 2>/dev/null | wc -l)
    zombie_count=$(ps -eo stat= 2>/dev/null | awk '$1 ~ /^Z/ { n++ } END { print n + 0 }')

    if (( proc_count > PROCESS_THRESHOLD )); then
        alert "process count ${proc_count} is above ${PROCESS_THRESHOLD}"
    fi

    if (( zombie_count > ZOMBIE_THRESHOLD )); then
        alert "zombie process count ${zombie_count} is above ${ZOMBIE_THRESHOLD}"
    fi
}

check_required_processes() {
    local name
    local -a wanted=()

    [[ -z $REQUIRED_PROCESSES ]] && return

    IFS=',' read -r -a wanted <<<"$REQUIRED_PROCESSES"

    for name in "${wanted[@]}"; do
        name=${name// /}
        [[ -z $name ]] && continue

        if ! pgrep -x "$name" >/dev/null 2>&1; then
            alert "required process '${name}' is not running"
        fi
    done
}

main() {
    case ${1:-} in
        -h | --help)
            usage
            exit 0
            ;;
        "")
            ;;
        *)
            echo "unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac

    validate_config

    check_cpu
    check_memory
    check_disk
    check_processes
    check_required_processes

    local load status
    load=$(awk '{ print $1 ", " $2 ", " $3 }' /proc/loadavg)
    status=OK
    (( alerts > 0 )) && status=ALERT

    printf '%s host=%s status=%s cpu=%s%% mem=%s%% disk=%s%% procs=%s zombies=%s load=%s alerts=%s\n' \
        "$(date -u +%FT%TZ)" "$(hostname)" "$status" \
        "$cpu_usage" "$mem_usage" "$disk_usage" "$proc_count" "$zombie_count" \
        "$load" "$alerts" | tee -a "$LOG_FILE"

    if (( alerts > 0 )); then
        return 1
    fi

    return 0
}

main "$@"
