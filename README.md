# K8-Manifest-OCI — DeploymentReady

Kubernetes deployment for the **Dynatrace Operator v1.4.0** on an OCI-based cluster.
Fully staged pipeline with automated TLS certificate provisioning via **cert-manager v1.14.5**.
Available in two parallel implementations: raw `kubectl` manifests and a Helm-based pipeline.

**Status: DeploymentReady** — end-to-end deployment verified on k3s (Multipass) with all cert-manager, caBundle injection, and conversion webhook gates passing.

---

## Repository Structure

```
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
│       ├── 10-crds.yaml                       # Dynatrace CRDs (with caBundle injection annotation)
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
- Image pull secret for `public.ecr.aws/dynatrace/` (embedded in `50-dynakube-oci.yaml` / `20-dynakube-cr.yaml`)

---

## Deployment

### Option A — Helm Pipeline (Recommended)

```bash
cd helm-scripts/staged/
chmod +x install.sh
./install.sh
```

**Flags:**

| Flag | Description |
|------|-------------|
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

The script executes 7 ordered steps with readiness and safety gates between each:

| Step | File(s) | Gate |
|------|---------|------|
| 1 | `00-namespace.yaml` | — |
| 2 | `01-cert-manager-install.yaml` | Wait: controller + cainjector + webhook pods ready; endpoint polling (120s); controller restart |
| 3 | `10-crds.yaml` | — |
| 4 | `20-rbac.yaml` | — |
| 5 | `30-config-services.yaml` + `02-webhook-certificate.yaml` | Wait: CA cert ready; serving cert ready; secret existence check |
| 6 | `40-workloads-webhooks.yaml` | Wait: webhook pods ready |
| 7 | `50-dynakube-oci.yaml` | Gate: caBundle injected into DynaKube CRD (60s); then apply |

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

Wait ~60 seconds for namespace termination before re-running install.

---

## Verification

```bash
# cert-manager (expect 3 pods Running)
kubectl get pods -n cert-manager

# Dynatrace operator + webhook pods
kubectl get pods -n dynatrace

# Certificate chain status
kubectl get certificate -n dynatrace

# caBundle injected into DynaKube CRD conversion webhook
kubectl get crd dynakubes.dynatrace.com \
  -o jsonpath='{.spec.conversion.webhook.clientConfig.caBundle}' | head -c 60

# DynaKube CR status
kubectl get dynakubes -n dynatrace
```

**Expected state after successful deployment:**
- `cert-manager`, `cert-manager-cainjector`, `cert-manager-webhook` → `Running`
- `dynatrace-webhook` (×2) → `Running/Ready`
- `certificate/dynatrace-webhook-ca` → `True`
- `certificate/dynatrace-webhook` → `True`
- `secret/dynatrace-webhook-certs` → present
- `dynakube/dynakube` → created
- `dynatrace-operator` → may show `CrashLoopBackOff` without a live Dynatrace tenant token — this is expected in test environments

---

## Background: Why This Exists

The upstream Dynatrace Operator bundle ships with a webhook deployment that mounts an `emptyDir` volume for TLS certificates but includes no mechanism to provision those certificates. This caused the following failure chain on OCI:

1. Webhook pod starts, waits for a certificate secret that never appears
2. Health probes fail → pod stuck in restart loop → no ready endpoints
3. Kubernetes API server cannot reach the webhook → admission requests rejected
4. `DynaKube` resource creation blocked: `no endpoints available for service "dynatrace-webhook"`

**Solution:** Integrated **cert-manager v1.14.5** to automate TLS certificate lifecycle. Certificates are self-signed (no external CA required), auto-renewed 15 days before the 90-day expiry, and the CA bundle is injected into both `MutatingWebhookConfiguration` and `ValidatingWebhookConfiguration` — and into the DynaKube CRD conversion webhook — by the cert-manager cainjector.

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
- **Root cause:** The hand-crafted `01-cert-manager-install.yaml` was missing RBAC permissions for `/status` subresources (`certificates/status`, `issuers/status`, `clusterissuers/status`) and for `pods` and `configmaps` required by the controller's informer cache. This caused a v1.14.0 lister-cache bug: the controller started on an empty cluster, its List+Watch cache was populated with nothing, and newly created Certificate objects were never reflected — certificates were never issued.
- **Fix:** Replaced the entire hand-crafted `01-cert-manager-install.yaml` (547 lines) with the official cert-manager v1.14.5 release manifest from `cert-manager/cert-manager` GitHub releases (5580+ lines). All RBAC, CRDs, and components are now authoritative.
- Added cert-manager controller restart (`kubectl rollout restart`) after the endpoint gate as a precautionary informer cache sync.

### v5 — Proper CA Chain in webhook-certificate.yaml
- **Root cause:** The 2-resource setup (selfSigned ClusterIssuer + serving Certificate) worked in isolation but did not produce a proper CA bundle that cainjector could inject into webhook configurations. The `dynatrace-webhook-ca` and `dynatrace-webhook-ca-issuer` resources referenced in the deploy script did not exist.
- **Fix:** Rebuilt `02-webhook-certificate.yaml` as a 4-resource CA chain:
  1. `ClusterIssuer/dynatrace-selfsigned-bootstrap` — bootstrap selfSigned issuer
  2. `Certificate/dynatrace-webhook-ca` — root CA cert (`isCA: true`, 10-year duration), signed by bootstrap issuer
  3. `Issuer/dynatrace-webhook-ca-issuer` — namespace-scoped CA-backed issuer using the CA secret
  4. `Certificate/dynatrace-webhook` — webhook serving cert (90-day duration), signed by CA issuer, stored in `dynatrace-webhook-certs`
- Updated deploy script to wait for the CA cert first, then the serving cert, then verify the secret exists before continuing.

### v6 — caBundle Injection Gate for DynaKube CRD
- **Root cause:** Applying the DynaKube CR failed with `x509: certificate signed by unknown authority`. The DynaKube CRD has a conversion webhook pointing to the `dynatrace-webhook` service; the field `spec.conversion.webhook.clientConfig.caBundle` was empty because cainjector had no annotation telling it which certificate to use.
- **Fixes:**
  - Added `cert-manager.io/inject-ca-from: dynatrace/dynatrace-webhook` annotation to the DynaKube CRD metadata in `10-crds.yaml` so cainjector populates `caBundle` automatically
  - Added a polling gate in the deploy script (60s max) that verifies `caBundle` is non-empty before applying the DynaKube CR

### v7 — Helm Pipeline Added (DeploymentReady)
- **New:** Parallel Helm-based implementation in `helm-scripts/`
  - `generate_helm.py` — standalone generator that reads `dynakube_OCI.yaml`, detects features, and writes all staged files
  - `staged/00-cert-manager-values.yaml` — cert-manager v1.14.5 Helm values
  - `staged/10-operator-values.yaml` — dynatrace-operator v1.4.0 Helm values (`installCRDs: true`, `csidriver.enabled: false`)
  - `staged/install.sh` — staged install script with full parity: 3-pod waits, endpoint polling, controller restart, caBundle gate
  - `staged/uninstall.sh` — reverse-order teardown (DynaKube CR → operator → cert-manager)
- **End-to-end test:** Full deployment verified on k3s running in a Multipass VM. All 7 steps completed successfully including DynaKube CR application.

---

## cert-manager Reference

| Item | Value |
|------|-------|
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
|------|-------|
| Operator version | v1.4.0 |
| Operator image | `public.ecr.aws/dynatrace/dynatrace-operator:v1.4.0` |
| Target namespace | `dynatrace` |
| DynaKube mode | classicFullStack (no CSI driver) |
| Source CR | `dynakube_OCI.yaml` |
