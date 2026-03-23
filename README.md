# K8-Manifest-OCIO

Kubernetes deployment manifests for the **Dynatrace Operator v1.8.1** on an OCI-based cluster, including a fully staged deployment pipeline with automated TLS certificate provisioning via **cert-manager v1.14.0**.

---

## Purpose

This repository contains the manifests and tooling required to deploy the Dynatrace Operator into an OCI Kubernetes cluster. The deployment pipeline addresses a critical webhook TLS certificate provisioning failure that prevented the operator from starting in the original out-of-the-box manifest bundle.

The staged deployment approach ensures resources are created in dependency order, with health checks and readiness gates between each step to prevent race conditions.

---

## Repository Structure

```
K8-Manifest-OCI/
├── dynakube_OCI.yaml                        # Standalone DynaKube resource reference
└── dynatrace-manifests/
    ├── dynatrace-operator-bundle.yaml       # Original upstream operator bundle
    ├── split_manifests.py                   # Script used to split bundle into staged files
    ├── HOW-TO.md                            # Operational how-to guide
    ├── Makefile                             # Make targets for common operations
    └── staged/                              # Primary deployment directory
        ├── 00-namespace.yaml                # dynatrace namespace
        ├── 01-cert-manager-install.yaml     # cert-manager v1.14.0 (all components)
        ├── 02-webhook-certificate.yaml      # Self-signed ClusterIssuer + Certificate
        ├── 10-crds.yaml                     # Dynatrace CRDs
        ├── 20-rbac.yaml                     # Dynatrace RBAC
        ├── 30-config-services.yaml          # ConfigMaps and Services
        ├── 40-workloads-webhooks.yaml       # Operator + webhook deployments
        ├── 50-dynakube-oci.yaml             # DynaKube resource + image pull secret
        ├── apply-staged.sh                  # Unified deployment script (recommended)
        ├── rollback-staged.sh               # Rollback / teardown script
        ├── REVISIONS-BUG.md                 # Bug history and fix log
        ├── CERT-MANAGER-SETUP.md            # cert-manager architecture reference
        └── README-CERT-MANAGER.txt          # Quick start guide
```

---

## Deployment

### Prerequisites

- `kubectl` v1.21+ configured against the target OCI cluster
- Access to the `dynatrace` namespace (or cluster-admin)
- Image pull secret for `public.ecr.aws/dynatrace/` (already embedded in `50-dynakube-oci.yaml`)

### Run the Deployment

```bash
cd dynatrace-manifests/staged/
./apply-staged.sh --verbose
```

The script executes 7 ordered steps with readiness gates between each:

| Step | Manifest | Description |
|------|----------|-------------|
| 1 | `00-namespace.yaml` | Create namespaces |
| 2 | `01-cert-manager-install.yaml` | Deploy cert-manager (controller, cainjector, webhook) |
| 3 | `10-crds.yaml` | Install Dynatrace CRDs |
| 4 | `20-rbac.yaml` | Apply RBAC |
| 5 | `30-config-services.yaml` + `02-webhook-certificate.yaml` | Config, services, TLS certificate |
| 6 | `40-workloads-webhooks.yaml` | Operator and webhook deployments |
| 7 | `50-dynakube-oci.yaml` | DynaKube resource |

### Script Flags

| Flag | Description |
|------|-------------|
| `--dry-run` | Preview all kubectl commands without applying |
| `--verbose` | Print detailed output at each step |
| `--skip-wait` | Skip readiness wait loops (faster, less safe) |
| `--skip-cert-manager` | Skip Steps 2 and 5 (if cert-manager already installed) |
| `--namespace <ns>` | Override target namespace (default: `dynatrace`) |
| `--help` | Show usage |

### Rollback

```bash
cd dynatrace-manifests/staged/
./rollback-staged.sh
```

---

## Verification

After deployment, confirm all components are healthy:

```bash
# cert-manager pods (should show 3/3 Running)
kubectl get pods -n cert-manager

# Dynatrace pods (operator + webhook)
kubectl get pods -n dynatrace

# TLS certificate status
kubectl get certificate dynatrace-webhook -n dynatrace

# DynaKube status
kubectl get dynakube -n dynatrace
```

---

## Background: The Webhook TLS Problem

The upstream Dynatrace Operator bundle ships with a webhook deployment that mounts an `emptyDir` volume for TLS certificates — but includes no mechanism to actually provision those certificates. This caused the following failure chain on OCI:

1. Webhook pod starts, waits indefinitely for a certificate secret that never appears
2. Health probes fail → pod stuck in restart loop → no ready endpoints
3. Kubernetes API server cannot reach the webhook → admission requests rejected
4. `DynaKube` resource creation blocked with: `no endpoints available for service "dynatrace-webhook"`

**Solution:** Integrated **cert-manager v1.14.0** to automate TLS certificate lifecycle. Certificates are self-signed (no external CA required), auto-renewed 15 days before the 90-day expiry, and the CA bundle is automatically injected into both `MutatingWebhookConfiguration` and `ValidatingWebhookConfiguration` resources.

---

## Fix History

### v1 — Initial cert-manager Integration
- Added `01-cert-manager-install.yaml` (controller only)
- Created `02-webhook-certificate.yaml` (ClusterIssuer + Certificate)
- Modified `40-workloads-webhooks.yaml`: replaced `emptyDir` with secret volume mount, added CA injection annotations
- Enhanced `apply-staged.sh` with cert-manager steps and runtime flags

### v2 — Complete cert-manager Component Set
- **Root cause of v2 failure:** Only the controller was deployed; `cert-manager-cainjector` and `cert-manager-webhook` components were missing
- Added cainjector (SA, ClusterRole, ClusterRoleBinding, Role, RoleBinding, Deployment)
- Added cert-manager webhook (SA, ClusterRole, ClusterRoleBinding, Role, RoleBinding, Service, Deployment, ValidatingWebhookConfiguration)
- Fixed broken certificate readiness wait in `apply-staged.sh` (label selector bug → resource name)

### v3 — ACME CRDs, RBAC Completion & Endpoint Gate *(current)*
- **Root cause of v3 failure:** Three distinct errors from deployment logs:
  1. Controller forbidden on `orders.acme.cert-manager.io` and `challenges.acme.cert-manager.io` → ACME CRDs were missing entirely
  2. Controller forbidden on `services` and `ingresses.networking.k8s.io` → RBAC gaps in `cert-manager-controller-issuers` ClusterRole
  3. `no endpoints available for service "cert-manager-webhook"` when applying certificate → timing race between webhook pod Running and webhook endpoint becoming available
- **Fixes applied to `01-cert-manager-install.yaml`:**
  - Added CRD `orders.acme.cert-manager.io` (Namespaced)
  - Added CRD `challenges.acme.cert-manager.io` (Namespaced)
  - Added RBAC rules to controller ClusterRole:
    ```yaml
    - apiGroups: [acme.cert-manager.io]
      resources: [orders, challenges]
      verbs: [create, delete, deletecollection, get, list, patch, update, watch]
    - apiGroups: [""]
      resources: [services]
      verbs: [get, list, watch]
    - apiGroups: [networking.k8s.io]
      resources: [ingresses]
      verbs: [get, list, watch]
    ```
- **Fix applied to `apply-staged.sh`:** Added endpoint polling gate (120s max) after cert-manager-webhook pod readiness check, before certificate creation — prevents race condition where pod is Running but endpoint not yet registered

---

## cert-manager Reference

| Item | Value |
|------|-------|
| Version | v1.14.0 |
| Components | controller, cainjector, webhook |
| CRDs | `certificates`, `issuers`, `clusterissuers`, `certificaterequests`, `orders`, `challenges` |
| Issuer type | SelfSigned (no external CA) |
| Certificate validity | 90 days |
| Auto-renew window | 15 days before expiry |
| Secret name | `dynatrace-webhook-certs` |
| Namespace | `cert-manager` |

---

## Dynatrace Reference

| Item | Value |
|------|-------|
| Operator version | v1.8.1 |
| Image | `public.ecr.aws/dynatrace/dynatrace-operator:v1.8.1` |
| Target namespace | `dynatrace` |
