# Quick Start Guide - Cert-Manager Setup

## Files Modified/Created

### ✅ NEW FILES
- **01-cert-manager-install.yaml** - cert-manager v1.14.0 deployment
- **02-webhook-certificate.yaml** - Self-signed issuer + certificate resource
- **CERT-MANAGER-SETUP.md** - Complete reference guide

### 🔧 MODIFIED FILES
- **apply-staged.sh** - Unified deployment script with cert-manager integration and flags
  - Supports `--dry-run`, `--verbose`, `--skip-cert-manager`, `--skip-wait`, `--namespace`, `--help`
  - Preserves original workflow while adding cert-manager steps
- **40-workloads-webhooks.yaml** - For cert-manager integration
  - Added pod annotation: `cert-manager.io/inject-ca-from: dynatrace/dynatrace-webhook`
  - Changed volume from emptyDir to secret mount
  - Added CA injection annotations to MutatingWebhookConfiguration
  - Added CA injection annotations to ValidatingWebhookConfiguration

## Deployment (Choose One)

### Option A: Automated (RECOMMENDED)
```bash
cd dynatrace-manifests/staged/
./apply-staged.sh
```

### Option B: Automated with Flags
```bash
# Preview only
./apply-staged.sh --dry-run

# Verbose logs for troubleshooting
./apply-staged.sh --verbose

# Faster execution without waits
./apply-staged.sh --skip-wait

# Custom namespace
./apply-staged.sh --namespace dynatrace
```

### Option C: Manual Step-by-Step
```bash
cd dynatrace-manifests/staged/

# Install cert-manager first
kubectl apply -f 00-namespace.yaml
kubectl apply -f 01-cert-manager-install.yaml
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=cert-manager -n cert-manager --timeout=300s

# Then deploy Dynatrace components
kubectl apply -f 10-crds.yaml
kubectl apply -f 20-rbac.yaml
kubectl apply -f 30-config-services.yaml
kubectl apply -f 02-webhook-certificate.yaml
kubectl wait --for=condition=Ready certificate/dynatrace-webhook -n dynatrace --timeout=120s

# Deploy webhook and operator
kubectl apply -f 40-workloads-webhooks.yaml
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/component=webhook -n dynatrace --timeout=300s

# Finally, create DynaKube
kubectl apply -f 50-dynakube-oci.yaml
```

## Check Status

```bash
# All-in-one status check
echo "=== Cert-Manager ===" && kubectl get pods -n cert-manager && \
echo -e "\n=== Certificate ===" && kubectl get certificate -n dynatrace && \
echo -e "\n=== Webhook Pods ===" && kubectl get pods -n dynatrace -l app.kubernetes.io/component=webhook && \
echo -e "\n=== Service Endpoints ===" && kubectl get endpoints dynatrace-webhook -n dynatrace && \
echo -e "\n=== DynaKube ===" && kubectl get dynakubes -n dynatrace

# View webhook logs (should NOT show certificate errors)
kubectl logs -n dynatrace -l app.kubernetes.io/component=webhook -c webhook --tail=30
```

## Expected Outcome

✅ Webhook pods → Running (not CrashLoopBackOff)
✅ Certificate resource → Ready status
✅ Webhook service → Has endpoints
✅ DynaKube → Created successfully
✅ Liveness probes → Passing

## Troubleshooting

**Still stuck on "waiting for certificate secret"?**
```bash
# Verify cert-manager is running
kubectl get pods -n cert-manager

# Check certificate status
kubectl describe certificate dynatrace-webhook -n dynatrace

# Check cert-manager logs
kubectl logs -n cert-manager -l app.kubernetes.io/name=cert-manager --tail=50
```

**Service has no endpoints?**
```bash
# Verify webhook pod labels
kubectl get pods -n dynatrace -l app.kubernetes.io/component=webhook --show-labels

# Check service selector
kubectl get svc dynatrace-webhook -n dynatrace -o jsonpath='{.spec.selector}'
```

## Full Documentation
See `CERT-MANAGER-SETUP.md` for complete details, architecture diagram, and verification steps.

Note: `apply-staged-with-cert-manager.sh` is still present as a legacy helper, but the preferred script is `apply-staged.sh`.
