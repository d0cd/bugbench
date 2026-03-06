#!/usr/bin/env bash
# Run curation in parallel across all repos, with interleaved prefixed output.
# Large repos (leo, snarkVM, snarkOS) are split into 3 shards each.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT}"

# Kill all background children on Ctrl+C or termination
cleanup() {
    echo ""
    echo "Caught signal — stopping all background jobs..."
    kill 0
    wait
    echo "Stopped. Checkpoints saved; re-run the script to resume."
    exit 1
}
trap cleanup INT TERM

curate_one() {
    local cands="$1" outdir="$2" shard="${3:-}"
    local shard_flag=()
    [[ -n "${shard}" ]] && shard_flag=(--shard "${shard}")
    PYTHONUNBUFFERED=1 uv run bugeval curate \
        --candidates "candidates/${cands}" \
        --output-dir "cases/${outdir}" \
        --fail-after 5 --api-delay 0.5 \
        "${shard_flag[@]}" 2>&1
}

prefix() {
    local name="$1"
    while IFS= read -r line; do
        printf "[%-14s] %s\n" "${name}" "${line}"
    done
}

echo "Starting parallel curation across all repos..."
echo "=============================================="

# Large repos — 3 shards each (own output dir per shard, no checkpoint races)
for k in 0 1 2; do
    curate_one ProvableHQ-leo-git-candidates.yaml     "leo-g${k}"    "${k}/3" | prefix "leo-git-${k}" &
    curate_one ProvableHQ-snarkVM-git-candidates.yaml "snarkvm-g${k}" "${k}/3" | prefix "snarkvm-git-${k}" &
    curate_one ProvableHQ-snarkOS-git-candidates.yaml "snarkos-g${k}" "${k}/3" | prefix "snarkos-git-${k}" &
done

# GitHub-scraped candidates for large repos (small enough for 1 worker each)
curate_one ProvableHQ-leo.yaml    leo-gh    | prefix "leo-gh"    &
curate_one ProvableHQ-snarkVM.yaml snarkvm-gh | prefix "snarkvm-gh" &
curate_one ProvableHQ-snarkOS.yaml snarkos-gh | prefix "snarkos-gh" &

# Small repos — 1 worker each
curate_one ProvableHQ-sdk-git-candidates.yaml sdk-git | prefix "sdk-git" &
curate_one ProvableHQ-sdk.yaml                sdk-gh  | prefix "sdk-gh"  &
curate_one calcom-cal.com.yaml      calcom    | prefix "calcom"    &
curate_one discourse-discourse.yaml discourse | prefix "discourse" &
curate_one getsentry-sentry.yaml    sentry    | prefix "sentry"    &
curate_one grafana-grafana.yaml     grafana   | prefix "grafana"   &
curate_one keycloak-keycloak.yaml   keycloak  | prefix "keycloak"  &

wait
echo "=============================================="
echo "All done. Cases written to cases/"
