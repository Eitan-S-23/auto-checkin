#!/usr/bin/env bash
# Userspace WARP: no system installation, route changes, or shared private keys.
set -euo pipefail
umask 077
unset SSLKEYLOGFILE

project_root="$(git -c core.fsmonitor=false --no-optional-locks rev-parse --show-toplevel)"
project_root="$(realpath -e -- "$project_root")"
cd "$project_root"
runtime="$project_root/.cache/nodeseek/warp"

# Validate existing symlinks as well as not-yet-created output paths before writing.
for name in '' wgcf wireproxy wireproxy.tar.gz wgcf-account.toml wgcf-profile.conf \
    wireproxy.conf register.log generate.log wireproxy.log wireproxy.pid
do
    target="$(realpath -m -- "$runtime/${name}")"
    case "$target" in
        "$project_root"/*) ;;
        *) printf 'WARP output resolves outside the project; refusing to write.\n' >&2
           exit 1 ;;
    esac
done

stop_proxy() {
    if [[ -f "$runtime/wireproxy.pid" ]]
    then
        read -r pid < "$runtime/wireproxy.pid"
        if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
        then
            executable="$(readlink -f -- "/proc/$pid/exe" || true)"
            if [[ "$executable" == "$runtime/wireproxy" ]]
            then
                kill "$pid" 2>/dev/null || true
            fi
        fi
        rm -f -- "$runtime/wireproxy.pid"
    fi
}

case "${1:-start}" in
    stop) stop_proxy
          exit 0 ;;
    start) ;;
    *) printf 'Usage: bash nodeseek/warp_proxy.sh [start|stop]\n' >&2
       exit 1 ;;
esac

if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]
then
    printf 'Built-in WARP requires a Linux x86_64 runner.\n' >&2
    exit 1
fi

mkdir -p "$runtime"
stop_proxy
trap stop_proxy EXIT

download() {
    local url="$1" destination="$2" digest="$3"
    curl --fail --silent --show-error --location --retry 2 --connect-timeout 15 \
        --max-time 120 --proxy '' --noproxy '*' "$url" --output "$destination"
    if ! printf '%s  %s\n' "$digest" "$destination" | sha256sum --check --status
    then
        printf 'WARP dependency checksum mismatch; refusing to execute the download.\n' >&2
        return 1
    fi
}

# Pin upstream releases and their independently retrieved GitHub release digests.
download 'https://github.com/ViRb3/wgcf/releases/download/v2.3.1/wgcf_2.3.1_linux_amd64' \
    "$runtime/wgcf" '4fb7a136aa470ece11d17949c7db07b040ec9c604742dc8680bcf1889f85d0c8'
download 'https://github.com/windtf/wireproxy/releases/download/v1.1.3/wireproxy_linux_amd64.tar.gz' \
    "$runtime/wireproxy.tar.gz" 'e88c1d090740373fc606c1bafd81d9a5eadc642cce5667616e20e9d7a444f51c'
# Stream just the binary into a preflighted file, never extract archive-controlled paths.
tar -xOf "$runtime/wireproxy.tar.gz" wireproxy > "$runtime/wireproxy"
chmod 700 "$runtime/wgcf" "$runtime/wireproxy"

if [[ ! -s "$runtime/wgcf-account.toml" ]]
then
    if ! timeout 90 "$runtime/wgcf" --config "$runtime/wgcf-account.toml" \
        register --accept-tos > "$runtime/register.log" 2>&1
    then
        printf 'WARP registration failed or timed out; private logs are not published.\n' >&2
        exit 1
    fi
fi
if ! timeout 60 "$runtime/wgcf" --config "$runtime/wgcf-account.toml" generate \
    --profile "$runtime/wgcf-profile.conf" --keepalive 25 > "$runtime/generate.log" 2>&1
then
    printf 'WARP profile generation failed or timed out; private logs are not published.\n' >&2
    exit 1
fi
chmod 600 "$runtime/wgcf-account.toml" "$runtime/wgcf-profile.conf"
printf 'WGConfig = %s\n\n[Socks5]\nBindAddress = 127.0.0.1:40000\n\n[Resolve]\nResolveStrategy = ipv4\n' \
    "$runtime/wgcf-profile.conf" > "$runtime/wireproxy.conf"

trace_url='https://www.cloudflare.com/cdn-cgi/trace'
direct="$(curl --fail --silent --show-error --connect-timeout 5 --max-time 10 \
    --proxy '' --noproxy '*' "$trace_url" || true)"
printf 'Runner direct route (only public trace fields):\n'
printf '%s\n' "$direct" | grep -E '^(ip=[0-9a-fA-F:.]+|warp=(off|on|plus)|colo=[A-Z]{3})$' || true

"$runtime/wireproxy" -c "$runtime/wireproxy.conf" > "$runtime/wireproxy.log" 2>&1 &
proxy_pid="$!"
printf '%s\n' "$proxy_pid" > "$runtime/wireproxy.pid"

for attempt in {1..12}
do
    if ! kill -0 "$proxy_pid" 2>/dev/null
    then
        printf 'The owned WARP proxy process exited before readiness verification.\n' >&2
        exit 1
    fi
    trace="$(curl --fail --silent --show-error --connect-timeout 5 --max-time 10 \
        --proxy socks5h://127.0.0.1:40000 --noproxy '' "$trace_url" 2>/dev/null || true)"
    if printf '%s\n' "$trace" | grep -qE '^warp=(on|plus)$'
    then
        printf 'WARP SOCKS5 proxy verified:\n'
        printf '%s\n' "$trace" | grep -E '^(ip=[0-9a-fA-F:.]+|warp=(on|plus)|colo=[A-Z]{3})$'
        trap - EXIT
        exit 0
    fi
    sleep 3
done
printf 'WARP did not become usable; refusing to silently fall back to the runner IP.\n' >&2
exit 1
