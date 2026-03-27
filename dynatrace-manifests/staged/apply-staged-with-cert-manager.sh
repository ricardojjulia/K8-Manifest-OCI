#!/bin/bash
set -euo pipefail

echo "=========================================="
echo "Deploying Dynatrace Operator with cert-manager"
echo "=========================================="

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Step 1: Apply namespace
echo -e "${BLUE}[1/6]${NC} Applying namespace..."
kubectl apply -f 00-namespace.yaml
echo -e "${GREEN}✓ Namespace applied${NC}"

# Step 2: Apply cert-manager
echo -e "${BLUE}[2/6]${NC} Installing cert-manager..."
kubectl apply -f 01-cert-manager-install.yaml
echo -e "${GREEN}✓ Cert-manager installing (wait for controller pod to be ready)${NC}"

# Wait for all three cert-manager pods to be ready
echo "Waiting for cert-manager controller to be ready..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=cert-manager -n cert-manager --timeout=300s

echo "Waiting for cert-manager cainjector to be ready..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=cainjector -n cert-manager --timeout=300s

echo "Waiting for cert-manager webhook to be ready..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=webhook -n cert-manager --timeout=300s

# Poll until cert-manager-webhook Service has a live endpoint (max 120s).
# This gate prevents the CA injection race: the webhook endpoint must be
# reachable before cert-manager can validate and issue Certificate resources.
echo "Polling cert-manager-webhook endpoint availability (max 120s)..."
DEADLINE=$(( $(date +%s) + 120 ))
while true; do
  EP=$(kubectl get endpoints cert-manager-webhook \
         -n cert-manager \
         -o jsonpath='{.subsets[0].addresses[0].ip}' 2>/dev/null || true)
  if [[ -n "$EP" ]]; then
    echo -e "  ${GREEN}cert-manager-webhook endpoint ready: $EP${NC}"
    break
  fi
  if [[ $(date +%s) -ge $DEADLINE ]]; then
    echo -e "  ${YELLOW}WARNING: cert-manager-webhook endpoint not ready after 120s.${NC}" >&2
    exit 1
  fi
  sleep 5
done

# Step 3: Apply CRDs
echo -e "${BLUE}[3/6]${NC} Applying CRDs..."
kubectl apply -f 10-crds.yaml
echo -e "${GREEN}✓ CRDs applied${NC}"

# Step 4: Apply RBAC
echo -e "${BLUE}[4/6]${NC} Applying RBAC..."
kubectl apply -f 20-rbac.yaml
echo -e "${GREEN}✓ RBAC applied${NC}"

# Step 5: Apply services, webhook certificate, and workloads
echo -e "${BLUE}[5/6]${NC} Applying webhook certificate configuration..."
kubectl apply -f 30-config-services.yaml
kubectl apply -f 02-webhook-certificate.yaml
echo -e "${GREEN}✓ Webhook certificate created${NC}"

# Poll until cert-manager cainjector has injected its own caBundle into the
# cert-manager ValidatingWebhookConfiguration. Without this gate, applying
# cert-manager Certificate/Issuer resources fails with:
#   x509: certificate signed by unknown authority
# because the API server cannot verify the cert-manager webhook's TLS cert.
echo "Waiting for cainjector to inject caBundle into cert-manager webhook (max 120s)..."
DEADLINE=$(( $(date +%s) + 120 ))
while true; do
  CABUNDLE=$(kubectl get validatingwebhookconfiguration cert-manager-webhook \
    -o jsonpath='{.webhooks[0].clientConfig.caBundle}' 2>/dev/null || true)
  if [[ -n "$CABUNDLE" ]]; then
    echo -e "  ${GREEN}cert-manager webhook caBundle injected${NC}"
    break
  fi
  if [[ $(date +%s) -ge $DEADLINE ]]; then
    echo "ERROR: cert-manager webhook caBundle not injected after 120s." >&2
    echo "       Check: kubectl describe validatingwebhookconfiguration cert-manager-webhook" >&2
    exit 1
  fi
  sleep 3
done

# Restart the cert-manager controller so its informer does a fresh List
# against the API server, which now includes the certificates we just created.
# Without this, the controller's lister cache (populated at startup on an
# empty cluster) never reflects the newly created Certificate objects.
echo "Restarting cert-manager controller to sync informer cache..."
kubectl rollout restart deployment/cert-manager -n cert-manager
kubectl rollout status deployment/cert-manager -n cert-manager --timeout=60s

# Wait for the CA certificate first — dynatrace-webhook depends on it.
# If dynatrace-webhook-ca does not exist (old version), skip this gate.
echo "Waiting for CA certificate to be issued (dynatrace-webhook-ca)..."
if kubectl get certificate dynatrace-webhook-ca -n dynatrace &>/dev/null; then
  kubectl wait --for=condition=Ready certificate/dynatrace-webhook-ca -n dynatrace --timeout=120s
else
  echo "  dynatrace-webhook-ca not found — assuming single-cert setup, skipping CA wait."
fi

echo "Waiting for webhook serving certificate to be issued (dynatrace-webhook)..."
kubectl wait --for=condition=Ready certificate/dynatrace-webhook -n dynatrace --timeout=120s

# Hard gate: the secret must exist before workloads can mount it.
echo "Verifying dynatrace-webhook-certs secret exists..."
if ! kubectl get secret dynatrace-webhook-certs -n dynatrace &>/dev/null; then
  echo "ERROR: secret 'dynatrace-webhook-certs' not found in namespace 'dynatrace'." >&2
  echo "       The certificate was not issued. Check: kubectl describe certificate dynatrace-webhook -n dynatrace" >&2
  exit 1
fi
echo -e "  ${GREEN}dynatrace-webhook-certs secret present${NC}"

echo -e "${BLUE}[6/6]${NC} Deploying webhook and operator..."
kubectl apply -f 40-workloads-webhooks.yaml
echo -e "${GREEN}✓ Workloads deployed${NC}"

# Wait for webhook to be ready
echo "Waiting for webhook pods to be ready..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/component=webhook -n dynatrace --timeout=300s

# Wait for cainjector to populate the DynaKube CRD conversion webhook caBundle.
# Without this, applying the DynaKube CR fails with x509: certificate signed by
# unknown authority because the API server cannot verify the conversion webhook.
echo "Waiting for cainjector to inject caBundle into DynaKube CRD (max 60s)..."
DEADLINE=$(( $(date +%s) + 60 ))
while true; do
  CABUNDLE=$(kubectl get crd dynakubes.dynatrace.com \
    -o jsonpath='{.spec.conversion.webhook.clientConfig.caBundle}' 2>/dev/null || true)
  if [[ -n "$CABUNDLE" ]]; then
    echo -e "  ${GREEN}caBundle injected into DynaKube CRD${NC}"
    break
  fi
  if [[ $(date +%s) -ge $DEADLINE ]]; then
    echo "ERROR: caBundle not injected into DynaKube CRD after 60s." >&2
    echo "       Check: kubectl describe crd dynakubes.dynatrace.com" >&2
    exit 1
  fi
  sleep 3
done

# Step 6: Apply DynaKube
echo -e "${BLUE}[7/7]${NC} Applying DynaKube configuration..."
kubectl apply -f 50-dynakube-oci.yaml
echo -e "${GREEN}✓ DynaKube applied${NC}"

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""
echo "Check status:"
echo "  - Cert-manager: kubectl get pods -n cert-manager"
echo "  - Webhook pods: kubectl get pods -n dynatrace -l app.kubernetes.io/component=webhook"
echo "  - DynaKube: kubectl get dynakubes -n dynatrace"
echo ""
echo "View logs:"
echo "  - Webhook: kubectl logs -n dynatrace -l app.kubernetes.io/component=webhook"
echo "  - Webhook events: kubectl describe pod -n dynatrace -l app.kubernetes.io/component=webhook | grep -A 10 Events"
echo ""
