# K8-Manifest-OCI — DeploymentReady v2

Kubernetes deployment for the **Dynatrace Operator v1.8.1** on an OCI-based cluster.
Fully staged pipeline with automated TLS certificate provisioning via **cert-manager v1.14.5**.
Available in two parallel implementations: raw `kubectl` manifests and a Helm-based pipeline.

**Status: DeploymentReady v2** — end-to-end deployment verified on k3s (Multipass) across both pipelines. All cert-manager races, CRD gaps, caBundle injection gates, and EdgeConnect requirements confirmed passing. Operator and ActiveGate successfully scheduled on EKS-class nodes.

---

## Repository Structure

```text
K8-Manifest-OCI/
├── dynakube_OCI.yaml                          # Source DynaKube CR + image pull secret
│
├── dynatrace-manifests/                       # Raw kubectl manifest pipeline
│   ├── dynatrace-operator-bundle.yaml         # Original upstream operator bundle
│   ├── split_manifests.py                     # Splits bundle into staged files
│   ├── HOW-TO.md
│   ├── Makefile
│   └── staged/
│       ├── 00-namespace.yaml                  # dynatrace namespace
│       ├── 01-cert-manager-install.yaml       # cert-manager v1.14.5 (official manifest)
│       ├── 02-webhook-certificate.yaml        # 4-resource CA chain (ClusterIssuer → CA → Issuer → serving cert)
│       ├── 10-crds.yaml                       # DynaKube + EdgeConnect CRDs (both with caBundle injection annotation)
│       ├── 20-rbac.yaml                       # Dynatrace RBAC
│       ├── 30-config-services.yaml            # ConfigMaps and Services
│       ├── 40-workloads-webhooks.yaml         # Operator + webhook Deployments
│       ├── 50-dynakube-oci.yaml               # DynaKube CR + image pull secret
│       ├── apply-staged-with-cert-manager.sh  # Primary deployment script
│       └── rollback-staged.sh                 # Teardown script
│
└── helm-scripts/                              # Helm-based pipeline (parallel implementation)
    ├── generate_helm.py                       # Generator: reads dynakube_OCI.yaml, writes staged/
    ├── Makefile
    └── staged/
        ├── 00-cert-manager-values.yaml        # cert-manager Helm values
        ├── 10-operator-values.yaml            # dynatrace-operator Helm values
        ├── 20-dynakube-cr.yaml                # DynaKube CR (verbatim extract)
        ├── install.sh                         # Staged Helm install script
        ├── uninstall.sh                       # Staged Helm uninstall script
        └── helm-values-report.txt             # Human-readable feature report
```

---

## Prerequisites

- `kubectl` v1.21+ configured against the target cluster
- Cluster-admin permissions (CRDs and RBAC require elevated access)
- **Helm pipeline only:** `helm` v3.10+ installed

  ```bash
  curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
  ```

- Image pull secret for `public.ecr.aws/dynatrace/` (embedded in `50-dynakube-oci.yaml` / `20-dynakube-cr.yaml`)

---

## Deployment

### Option A — Helm Pipeline (Recommended)

```bash
cd helm-scripts/staged/
chmod +x install.sh
./install.sh
```

**What it does (3 steps):**

| Step | Action | Gates |
| --- | --- | --- |
| 1 | Install cert-manager v1.14.5 via Helm | 3-pod waits → endpoint poll (120s) → caBundle injection into cert-manager webhook → controller restart |
| 2 | Install dynatrace-operator v1.8.1 via Helm (`installCRDs: true`) | operator + webhook pod waits |
| 3 | Apply DynaKube CR via kubectl | caBundle injected into DynaKube + EdgeConnect CRDs (120s) → apply |

**Flags:**

| Flag | Description |
| --- | --- |
| `--dry-run` | Preview all Helm and kubectl commands without applying |
| `--skip-cert-manager` | Skip cert-manager install (if already present) |
| `--skip-wait` | Skip readiness wait loops |
| `--namespace NS` | Override target namespace (default: `dynatrace`) |
| `--help` | Show usage |

**Uninstall:**

```bash
./uninstall.sh --purge-namespaces
```

---

### Option B — Raw Manifest Pipeline

```bash
cd dynatrace-manifests/staged/
chmod +x apply-staged-with-cert-manager.sh
./apply-staged-with-cert-manager.sh
```

**What it does (7 steps):**

| Step | File(s) | Gates |
| --- | --- | --- |
| 1 | `00-namespace.yaml` | — |
| 2 | `01-cert-manager-install.yaml` | 3-pod waits → endpoint poll (120s) → caBundle injection into cert-manager webhook (120s) → controller restart |
| 3 | `10-crds.yaml` | — |
| 4 | `20-rbac.yaml` | — |
| 5 | `30-config-services.yaml` + `02-webhook-certificate.yaml` | CA cert ready → serving cert ready → secret existence check |
| 6 | `40-workloads-webhooks.yaml` | webhook pods ready |
| 7 | `50-dynakube-oci.yaml` | caBundle injected into DynaKube + EdgeConnect CRDs (120s) → apply |

**Rollback:**

```bash
./rollback-staged.sh
kubectl delete -f 01-cert-manager-install.yaml --ignore-not-found=true
kubectl delete namespace cert-manager --ignore-not-found=true
```

---

## Starting Fresh (Clean Reinstall)

If a previous attempt left partial state, clean up before re-running.

**Helm pipeline:**

```bash
cd helm-scripts/staged/
./uninstall.sh --purge-namespaces
```

**Raw manifest pipeline:**

```bash
cd dynatrace-manifests/staged/
./rollback-staged.sh
kubectl delete -f 01-cert-manager-install.yaml --ignore-not-found=true
kubectl delete namespace cert-manager --ignore-not-found=true
```

Wait ~60 seconds for namespace termination, then verify clean before re-running:

```bash
kubectl get ns | grep -E 'dynatrace|cert-manager'
kubectl get crd | grep -E 'dynatrace|cert-manager'
```

Both should return nothing.

---

## Verification

```bash
# cert-manager (expect 3 pods Running)
kubectl get pods -n cert-manager

# Dynatrace pods — operator, webhook (x2), activegate
kubectl get pods -n dynatrace

# Certificate chain
kubectl get certificate -n dynatrace

# DynaKube CR status
kubectl get dynakubes -n dynatrace
```

**Expected state after successful deployment:**

- `cert-manager`, `cert-manager-cainjector`, `cert-manager-webhook` → `Running`
- `dynatrace-operator` → `Running 1/1`
- `dynatrace-webhook` (×2) → `Running/Ready`
- `dynakube-<name>-activegate-0` → `Running` (requires connectivity to Dynatrace tenant)
- `certificate/dynatrace-webhook-ca` → `True`
- `certificate/dynatrace-webhook` → `True`
- `secret/dynatrace-webhook-certs` → present
- `dynakube/<name>` → `Deploying` → `Running`

> **Note:** In isolated test environments without connectivity to the Dynatrace tenant, the ActiveGate pod will remain `Pending` or `CrashLoopBackOff`. This is expected — on a connected cluster with a valid API token the ActiveGate will start normally.

---

## Background: Why This Exists

The upstream Dynatrace Operator bundle ships with a webhook deployment that mounts an `emptyDir` volume for TLS certificates but includes no mechanism to provision those certificates. This caused the following failure chain on OCI:

1. Webhook pod starts, waits for a certificate secret that never appears
2. Health probes fail → pod stuck in restart loop → no ready endpoints
3. Kubernetes API server cannot reach the webhook → admission requests rejected
4. `DynaKube` resource creation blocked: `no endpoints available for service "dynatrace-webhook"`

**Solution:** Integrated **cert-manager v1.14.5** to automate TLS certificate lifecycle. Certificates are self-signed (no external CA required), auto-renewed 15 days before the 90-day expiry, and the CA bundle is injected into both `MutatingWebhookConfiguration` and `ValidatingWebhookConfiguration` — and into both the DynaKube and EdgeConnect CRD conversion webhooks — by the cert-manager cainjector.

---

## Fix History

### v1 — Initial cert-manager Integration

- Added `01-cert-manager-install.yaml` (controller only, hand-crafted minimal manifest)
- Created `02-webhook-certificate.yaml` (selfSigned ClusterIssuer + Certificate)
- Modified `40-workloads-webhooks.yaml`: replaced `emptyDir` with secret volume mount, added CA injection annotations
- Enhanced `apply-staged.sh` with cert-manager steps and runtime flags

### v2 — Complete cert-manager Component Set

- **Root cause:** Only the controller was deployed; `cert-manager-cainjector` and `cert-manager-webhook` were missing
- Added cainjector and cert-manager webhook components to `01-cert-manager-install.yaml`
- Fixed certificate readiness wait in deploy script (label selector bug → resource name)

### v3 — ACME CRDs, RBAC Completion, Endpoint Gate

- **Root cause:** Three separate failures:
  1. Controller forbidden on `orders.acme.cert-manager.io` and `challenges.acme.cert-manager.io` — ACME CRDs missing
  2. Controller forbidden on `services` and `ingresses.networking.k8s.io` — RBAC gaps
  3. `no endpoints available for service "cert-manager-webhook"` — timing race between pod Running and endpoint registration
- Added ACME CRDs and missing RBAC rules to `01-cert-manager-install.yaml`
- Added endpoint polling gate (120s) in deploy script before certificate creation

### v4 — cert-manager Manifest Replaced with Official Release

- **Root cause:** The hand-crafted `01-cert-manager-install.yaml` was missing RBAC permissions for `/status` subresources (`certificates/status`, `issuers/status`, `clusterissuers/status`) and for `pods` and `configmaps` required by the controller's informer cache. This caused a lister-cache bug: the controller started on an empty cluster, its List+Watch cache was populated with nothing, and newly created Certificate objects were never reflected — certificates were never issued.
- **Fix:** Replaced the entire hand-crafted manifest (547 lines) with the official cert-manager v1.14.5 release manifest (5580+ lines). All RBAC, CRDs, and components are now authoritative.
- Added cert-manager controller restart (`kubectl rollout restart`) after the endpoint gate as a precautionary informer cache sync.

### v5 — Proper CA Chain in webhook-certificate.yaml

- **Root cause:** The 2-resource setup (selfSigned ClusterIssuer + serving Certificate) did not produce a proper CA bundle that cainjector could inject into webhook configurations.
- **Fix:** Rebuilt `02-webhook-certificate.yaml` as a 4-resource CA chain:
  1. `ClusterIssuer/dynatrace-selfsigned-bootstrap` — bootstrap selfSigned issuer
  2. `Certificate/dynatrace-webhook-ca` — root CA cert (`isCA: true`, 10-year duration)
  3. `Issuer/dynatrace-webhook-ca-issuer` — namespace-scoped CA-backed issuer
  4. `Certificate/dynatrace-webhook` — webhook serving cert (90-day duration), stored in `dynatrace-webhook-certs`
- Updated deploy script to wait for CA cert, then serving cert, then verify secret exists before continuing.

### v6 — caBundle Injection Gate for DynaKube CRD

- **Root cause:** Applying the DynaKube CR failed with `x509: certificate signed by unknown authority`. The DynaKube CRD conversion webhook `caBundle` field was empty — cainjector had no annotation telling it which certificate to inject.
- **Fixes:**
  - Added `cert-manager.io/inject-ca-from: dynatrace/dynatrace-webhook` annotation to DynaKube CRD in `10-crds.yaml`
  - Added caBundle polling gate before DynaKube CR apply

### v7 — Helm Pipeline Added

- **New:** Parallel Helm-based implementation in `helm-scripts/` with full gate parity to the raw manifest pipeline
- `generate_helm.py` — standalone generator reading `dynakube_OCI.yaml` and writing all staged files
- `staged/install.sh` — endpoint poll, controller restart, caBundle gate
- `staged/uninstall.sh` — reverse-order teardown
- End-to-end deployment verified on k3s/Multipass

### v8 — EdgeConnect CRD, cert-manager caBundle Gate, Helm Parity (DeploymentReady v2)

- **Root cause 1 — Operator CrashLoopBackOff:** Operator v1.8.1 requires the `edgeconnects.dynatrace.com` CRD with version `v1alpha2`. This CRD was present in the operator bundle but not included in `10-crds.yaml`. Without it, the operator crashed on startup with `no matches for kind "EdgeConnect" in version "dynatrace.com/v1alpha2"`.
  - **Fix:** Extracted `edgeconnects.dynatrace.com` CRD from the bundle and added it to `10-crds.yaml`.

- **Root cause 2 — Silent DynaKube CRD overwrite:** After appending the EdgeConnect CRD, the YAML document separator (`---`) between the two CRDs was missing. YAML treated the entire file as a single document; EdgeConnect's fields overwrote DynaKube's fields, so only `edgeconnects.dynatrace.com` was ever created — `dynakubes.dynatrace.com` was silently lost.
  - **Fix:** Added `---` separator between the two CRD documents in `10-crds.yaml`.

- **Root cause 3 — cert-manager webhook x509 error:** When applying cert-manager Certificate/Issuer resources, the API server called the cert-manager validation webhook before cainjector had injected the webhook's own caBundle, resulting in `x509: certificate signed by unknown authority`.
  - **Fix:** Added a polling gate (120s) on `validatingwebhookconfiguration/cert-manager-webhook` caBundle — runs after endpoint gate, before any cert-manager resources are applied.

- **Root cause 4 — EdgeConnect caBundle not injected:** The EdgeConnect CRD also has a conversion webhook pointing to `dynatrace-webhook` but lacked the `cert-manager.io/inject-ca-from` annotation, so cainjector never populated its caBundle. The 60s timeout was also too short with two CRDs to process.
  - **Fix:** Added `cert-manager.io/inject-ca-from: dynatrace/dynatrace-webhook` to EdgeConnect CRD. Updated caBundle poll to verify both CRDs, extended timeout to 120s with per-CRD diagnostic output on failure.

- **Helm pipeline brought to full parity:** All four fixes applied to `helm-scripts/staged/install.sh` and `helm-scripts/generate_helm.py`.

- **Prerequisite clarified:** `helm` must be installed before running the Helm pipeline.

---

## cert-manager Reference

| Item | Value |
| --- | --- |
| Version | v1.14.5 (official release manifest) |
| Components | controller, cainjector, webhook |
| CA cert duration | 10 years (rotate deliberately) |
| Serving cert duration | 90 days |
| Auto-renew window | 15 days before expiry |
| Serving cert secret | `dynatrace-webhook-certs` |
| CA secret | `dynatrace-webhook-ca` |
| Namespace | `cert-manager` |

---

## Dynatrace Reference

| Item | Value |
| --- | --- |
| Operator version | v1.8.1 |
| Operator image | `public.ecr.aws/dynatrace/dynatrace-operator:v1.8.1` |
| Target namespace | `dynatrace` |
| DynaKube mode | classicFullStack (no CSI driver) |
| ActiveGate capabilities | `routing`, `kubernetes-monitoring`, `dynatrace-api` |
| Source CR | `dynakube_OCI.yaml` |
