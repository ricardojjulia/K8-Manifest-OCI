# Dynatrace Manifest Splitter: HOW-TO

This guide explains what this project does and exactly how to run it.

## 1) What this does

The script takes one large Dynatrace operator bundle and splits it into ordered files so apply and rollback are safer.

Input:

- `dynatrace-operator-bundle.yaml`

Main output folder:

- `staged/`

Standard staged files:

1. `00-namespace.yaml`
2. `10-crds.yaml`
3. `20-rbac.yaml`
4. `30-config-services.yaml`
5. `40-workloads-webhooks.yaml`

Helper scripts:

- `staged/apply-staged.sh`
- `staged/rollback-staged.sh`

## 2) Prerequisites

- Python 3
- kubectl installed and configured to the correct cluster
- Permissions to apply CRDs, RBAC, and namespaced resources

## 3) Quick start

From this folder:

```bash
cd /Users/Ricardo.Julia/K8-Manifest-OCI/dynatrace-manifests
```

Generate staged manifests (standard mode):

```bash
python3 split_manifests.py
```

Apply in order:

```bash
./staged/apply-staged.sh
```

Rollback in reverse order:

```bash
./staged/rollback-staged.sh
```

## 4) Validation modes

Strict validation:

```bash
python3 split_manifests.py --strict
```

This fails if the source YAML has:

- empty docs
- missing `kind`
- unknown kinds (outside script allowlist)

## 5) OCI-focused mode

Use OCI mode when you want output to match what is defined in `../dynakube_OCI.yaml`.

```bash
python3 split_manifests.py --process4oci
```

In OCI mode, output also includes:

- `staged/50-dynakube-oci.yaml` (Secret + DynaKube docs from dynakube_OCI.yaml)
- `staged/oci-filter-report.txt` (what was kept and dropped)

Add machine-readable report:

```bash
python3 split_manifests.py --process4oci --report-json
```

This also writes:

- `staged/oci-filter-report.json`

## 6) OCI profile shortcuts

Force classic profile behavior:

```bash
python3 split_manifests.py --process4classic
```

Force cloud-native profile behavior:

```bash
python3 split_manifests.py --process4cloudnative
```

## 7) CI safety gate

Fail when OCI filtering drops any resources:

```bash
python3 split_manifests.py --process4oci --fail-on-drop
```

This is intentionally strict and useful for CI.

## 8) Makefile shortcuts

Show all shortcuts:

```bash
make help
```

Most useful targets:

```bash
make split
make split-strict
make oci
make oci-strict
make oci-json
make oci-fail-on-drop
make ci
```

`make ci` runs:

- OCI mode
- strict checks
- JSON report
- fail-on-drop

## 9) Typical workflows

Local developer workflow:

1. `make oci`
2. review `staged/oci-filter-report.txt`
3. `./staged/apply-staged.sh`

CI workflow:

1. `make ci`
2. publish `staged/oci-filter-report.json` as an artifact

## 10) Troubleshooting

If strict mode fails:

- read the error line numbers
- fix YAML separators or missing fields in source manifests

If fail-on-drop fails:

- check dropped list in `staged/oci-filter-report.txt`
- decide if those resources should remain excluded
- if not, update filtering logic in `split_manifests.py`

If kubectl apply fails:

- verify target context: `kubectl config current-context`
- verify CRDs exist: `kubectl get crd | grep dynatrace.com`
- inspect operator/webhook events in namespace `dynatrace`

## 11) Security note

Do not commit real API tokens to source control.

If `dynakube_OCI.yaml` contains real tokens, rotate them and move to a secret management process.
