# Dynatrace Operator Manifests (Staged Apply)

This repository contains a **single Dynatrace operator bundle** and a **staged version** of that bundle split into safer apply/delete phases.

For a clean quick-start guide, see `HOW-TO.md`.

## Why this exists

Applying one large Kubernetes manifest can fail when dependencies are not ready yet (for example, CRDs must exist before custom resources that rely on them).

This repo addresses that by splitting resources into an execution order:

1. Namespace
2. CRDs
3. RBAC
4. Config + Services
5. Workloads + Webhooks + remaining resources

This staged approach makes installs and rollbacks more predictable and easier to troubleshoot.

## Repository structure

- `dynatrace-operator-bundle.yaml` — original combined manifest (~11k lines).
- `split_manifests.py` — script that parses the bundle and writes staged files by resource `kind`.
- `00-namespace.yaml` — namespace-only manifest at repo root.
- `staged/` — ordered, split manifests and helper scripts:
  - `00-namespace.yaml`
  - `10-crds.yaml`
  - `20-rbac.yaml`
  - `30-config-services.yaml`
  - `40-workloads-webhooks.yaml`
  - `apply-staged.sh`
  - `rollback-staged.sh`

## Staging model

The split logic in `split_manifests.py` is kind-based:

- `Namespace` -> `00-namespace.yaml`
- `CustomResourceDefinition` -> `10-crds.yaml`
- `ClusterRole`, `ClusterRoleBinding`, `Role`, `RoleBinding`, `ServiceAccount` -> `20-rbac.yaml`
- `ConfigMap`, `Secret`, `Service` -> `30-config-services.yaml`
- Everything else -> `40-workloads-webhooks.yaml`

## Prerequisites

- `kubectl` installed and configured (`kubectl config current-context` points to the intended cluster).
- Permissions to create/update namespace-scoped and cluster-scoped resources (CRDs + RBAC require elevated permissions).
- Bash shell for helper scripts.

## Usage

### 1) Apply using staged order

From repository root:

```bash
chmod +x staged/apply-staged.sh staged/rollback-staged.sh
./staged/apply-staged.sh
```

This applies:

1. `00-namespace.yaml`
2. `10-crds.yaml`
3. `20-rbac.yaml`
4. `30-config-services.yaml`
5. `40-workloads-webhooks.yaml`

### 2) Roll back in reverse order

```bash
./staged/rollback-staged.sh
```

Rollback deletes in reverse order to reduce dependency-related delete errors.

## Re-generate the staged manifests

Run:

```bash
python3 split_manifests.py
```

This rebuilds the files under `staged/` from `dynatrace-operator-bundle.yaml` and rewrites `staged/apply-staged.sh`.
It also rewrites `staged/rollback-staged.sh` so apply and rollback scripts always stay in sync.

`split_manifests.py` uses repository-relative paths, so it can be run from this repo on any machine without path edits.

Optional overrides:

```bash
python3 split_manifests.py --src ./dynatrace-operator-bundle.yaml --out-dir ./staged
```

OCI-focused processing:

```bash
python3 split_manifests.py --process4oci
```

This mode reads `../dynakube_OCI.yaml` by default and filters the downloaded operator bundle down to the resources required by that DynaKube configuration. It also appends a final staged manifest:

- `50-dynakube-oci.yaml` — the `Secret` and `DynaKube` resources from `dynakube_OCI.yaml`
- `oci-filter-report.txt` — a summary of detected OCI features plus the bundle resources that were kept and dropped

You can override the Dynakube source file:

```bash
python3 split_manifests.py --process4oci --dynakube-file ../dynakube_OCI.yaml
```

Profile shortcuts:

```bash
python3 split_manifests.py --process4classic
python3 split_manifests.py --process4cloudnative
```

These behave like OCI mode, but force the OneAgent filtering profile to `classicFullStack` or `cloudNativeFullStack` respectively.

Machine-readable report:

```bash
python3 split_manifests.py --process4oci --report-json
```

This adds `oci-filter-report.json` alongside the text report.

CI guard to fail when any resources are excluded:

```bash
python3 split_manifests.py --process4oci --fail-on-drop
```

This is useful when you want OCI filtering to be explicit and reject silent drops.

When `--process4oci` is enabled, the generated apply order becomes:

1. `00-namespace.yaml`
2. `10-crds.yaml`
3. `20-rbac.yaml`
4. `30-config-services.yaml`
5. `40-workloads-webhooks.yaml`
6. `50-dynakube-oci.yaml`

Strict validation mode:

```bash
python3 split_manifests.py --strict
```

With `--strict`, the script fails fast if it detects empty YAML documents, missing `kind`, or unknown resource kinds.

### Makefile shortcuts

You can also use helper targets:

```bash
make split
make split-strict
make oci
make oci-strict
make oci-json
make oci-fail-on-drop
make ci
make oci-classic
make oci-classic-strict
make oci-cloudnative
make oci-cloudnative-strict
```

Show CLI help:

```bash
python3 split_manifests.py --help
```

## Verification tips

- Check namespace exists:

```bash
kubectl get ns dynatrace
```

- Check CRDs are registered:

```bash
kubectl get crd | grep dynatrace.com
```

- Check operator pods/services:

```bash
kubectl -n dynatrace get pods,svc
```

## Troubleshooting

- If apply fails at CRD-dependent objects, rerun from the failed stage after CRDs are established.
- If RBAC errors occur, verify cluster role privileges for the identity running `kubectl`.
- If webhooks fail to become ready, inspect:

```bash
kubectl -n dynatrace describe deploy dynatrace-operator
kubectl -n dynatrace get events --sort-by=.metadata.creationTimestamp
```
