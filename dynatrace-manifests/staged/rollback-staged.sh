#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for f in 50-dynakube-oci.yaml 40-workloads-webhooks.yaml 30-config-services.yaml 20-rbac.yaml 10-crds.yaml 00-namespace.yaml; do
  echo "Deleting $f"
  kubectl delete -f "$DIR/$f" --ignore-not-found=true
done
