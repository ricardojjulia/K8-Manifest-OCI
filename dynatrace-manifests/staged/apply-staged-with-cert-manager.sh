#!/bin/bash
set -e

echo "=========================================="
echo "Deploying Dynatrace Operator with cert-manager"
echo "=========================================="

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Step 1: Apply namespace
echo -e "${BLUE}[1/6]${NC} Applying namespace..."
kubectl apply -f 00-namespace.yaml
echo -e "${GREEN}✓ Namespace applied${NC}"

# Step 2: Apply cert-manager
echo -e "${BLUE}[2/6]${NC} Installing cert-manager..."
kubectl apply -f 01-cert-manager-install.yaml
echo -e "${GREEN}✓ Cert-manager installing (wait for controller pod to be ready)${NC}"

# Wait for cert-manager to be ready
echo "Waiting for cert-manager controller to be ready..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=cert-manager -n cert-manager --timeout=300s || true

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

echo "Waiting for certificate to be issued..."
kubectl wait --for=condition=Ready certificate/dynatrace-webhook -n dynatrace --timeout=120s || true

echo -e "${BLUE}[6/6]${NC} Deploying webhook and operator..."
kubectl apply -f 40-workloads-webhooks.yaml
echo -e "${GREEN}✓ Workloads deployed${NC}"

# Wait for webhook to be ready
echo "Waiting for webhook pods to be ready..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/component=webhook -n dynatrace --timeout=300s || true

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
