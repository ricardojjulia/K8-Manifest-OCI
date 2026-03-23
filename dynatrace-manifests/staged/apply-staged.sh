#!/usr/bin/env bash
################################################################################
# Dynatrace Operator Deployment Script with cert-manager Integration
# 
# Enhanced version that accepts flags and arguments while maintaining original
# workflow. Automatically handles webhook certificate provisioning via cert-manager.
#
# Usage: ./apply-staged.sh [OPTIONS]
# Examples:
#   ./apply-staged.sh                           # Default deployment
#   ./apply-staged.sh --dry-run                 # Preview changes
#   ./apply-staged.sh --verbose                 # Detailed output
#   ./apply-staged.sh --skip-cert-manager       # Advanced: skip cert-manager
#   ./apply-staged.sh --help                    # Show help
################################################################################

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Script directory
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Default options
DRY_RUN=false
VERBOSE=false
SKIP_CERT_MANAGER=false
SKIP_WAIT=false
HELP=false
CUSTOM_API_URL=""
CUSTOM_TOKENS=""
NAMESPACE="dynatrace"

################################################################################
# Function: Print help message
################################################################################
print_help() {
  cat << EOF
${BLUE}Dynatrace Operator Deployment Script${NC}

${GREEN}USAGE:${NC}
  ./apply-staged.sh [OPTIONS]

${GREEN}OPTIONS:${NC}
  --dry-run               Preview changes without applying (shows kubectl diff)
  --verbose               Output detailed information during deployment
  --skip-cert-manager     Skip cert-manager installation (advanced/manual cert setup)
  --skip-wait             Skip health checks and wait steps (faster but riskier)
  --namespace NS          Target namespace (default: dynatrace)
  --help                  Show this help message and exit

${GREEN}ENVIRONMENT VARIABLES:${NC}
  KUBECTL_CONTEXT         Override kubectl context (optional)

${GREEN}EXAMPLES:${NC}
  # Standard deployment (recommended)
  ./apply-staged.sh

  # Preview what will be applied
  ./apply-staged.sh --dry-run

  # Verbose output for troubleshooting
  ./apply-staged.sh --verbose

  # Advanced: Skip cert-manager and provide own certs
  ./apply-staged.sh --skip-cert-manager

  # Faster deployment (no health checks)
  ./apply-staged.sh --skip-wait

${GREEN}WORKFLOW:${NC}
  1. Namespace creation
  2. Cert-manager installation (unless --skip-cert-manager)
  3. Dynatrace CRDs
  4. RBAC configuration
  5. Services and webhook certificates
  6. Webhook and operator deployments
  7. DynaKube resource creation

EOF
}

################################################################################
# Function: Print colored output
################################################################################
log_info() {
  echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
  echo -e "${GREEN}[✓]${NC} $*"
}

log_warning() {
  echo -e "${YELLOW}[WARNING]${NC} $*"
}

log_error() {
  echo -e "${RED}[ERROR]${NC} $*"
}

################################################################################
# Function: Parse command-line arguments
################################################################################
parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case $1 in
      --dry-run)
        DRY_RUN=true
        log_info "Dry-run mode enabled (will show diffs, no changes applied)"
        shift
        ;;
      --verbose)
        VERBOSE=true
        log_info "Verbose mode enabled"
        shift
        ;;
      --skip-cert-manager)
        SKIP_CERT_MANAGER=true
        log_warning "Cert-manager will be skipped (you must provision certificates manually)"
        shift
        ;;
      --skip-wait)
        SKIP_WAIT=true
        log_info "Health checks will be skipped (faster but riskier)"
        shift
        ;;
      --namespace)
        NAMESPACE="$2"
        log_info "Using namespace: $NAMESPACE"
        shift 2
        ;;
      --help|-h)
        HELP=true
        shift
        ;;
      *)
        log_error "Unknown option: $1"
        echo ""
        print_help
        exit 1
        ;;
    esac
  done
}

################################################################################
# Function: Apply a manifest file
################################################################################
apply_manifest() {
  local manifest_file="$1"
  local description="$2"
  
  if [[ ! -f "$DIR/$manifest_file" ]]; then
    log_error "Manifest not found: $DIR/$manifest_file"
    exit 1
  fi
  
  log_info "Applying $description ($manifest_file)..."
  
  if [[ "$DRY_RUN" == "true" ]]; then
    log_info "[DRY-RUN] Would apply: $manifest_file"
    if [[ "$VERBOSE" == "true" ]]; then
      kubectl diff -f "$DIR/$manifest_file" 2>/dev/null || log_info "No differences or resource doesn't exist yet"
    fi
  else
    if [[ "$VERBOSE" == "true" ]]; then
      kubectl apply -f "$DIR/$manifest_file" --record=true
    else
      kubectl apply -f "$DIR/$manifest_file" > /dev/null 2>&1
    fi
    log_success "$description applied"
  fi
}

################################################################################
# Function: Wait for resource to be ready
################################################################################
wait_for_resource() {
  local resource_type="$1"
  local label_selector="$2"
  local namespace="$3"
  local timeout="${4:-120}"
  
  if [[ "$DRY_RUN" == "true" ]] || [[ "$SKIP_WAIT" == "true" ]]; then
    return 0
  fi
  
  log_info "Waiting for $resource_type to be ready (timeout: ${timeout}s)..."
  
  if kubectl wait --for=condition=Ready $resource_type -l "$label_selector" -n "$namespace" --timeout="${timeout}s" 2>/dev/null; then
    log_success "$resource_type ready"
  else
    log_warning "Timeout waiting for $resource_type, proceeding anyway"
  fi
}

################################################################################
# Function: Verify prerequisites
################################################################################
verify_prerequisites() {
  log_info "Verifying prerequisites..."
  
  # Check kubectl
  if ! command -v kubectl &> /dev/null; then
    log_error "kubectl not found in PATH"
    exit 1
  fi
  log_success "kubectl found"
  
  # Check cluster access
  if [[ "$DRY_RUN" != "true" ]]; then
    if ! kubectl cluster-info &> /dev/null; then
      log_error "Cannot access Kubernetes cluster"
      exit 1
    fi
    log_success "Kubernetes cluster accessible"
  fi
  
  # Check namespace
  if [[ "$DRY_RUN" != "true" ]]; then
    if ! kubectl get namespace "$NAMESPACE" &> /dev/null; then
      log_warning "Namespace $NAMESPACE does not exist yet (will be created)"
    fi
  fi
}

################################################################################
# Main deployment workflow
################################################################################
main() {
  echo ""
  echo "=========================================="
  echo "Dynatrace Operator Deployment"
  echo "=========================================="
  echo ""
  
  # Parse arguments
  parse_arguments "$@"
  
  # Show help if requested
  if [[ "$HELP" == "true" ]]; then
    print_help
    exit 0
  fi
  
  # Show configuration
  if [[ "$VERBOSE" == "true" ]]; then
    echo -e "${BLUE}Configuration:${NC}"
    echo "  Directory: $DIR"
    echo "  Namespace: $NAMESPACE"
    echo "  Dry-run: $DRY_RUN"
    echo "  Skip cert-manager: $SKIP_CERT_MANAGER"
    echo "  Skip waits: $SKIP_WAIT"
    echo ""
  fi
  
  # Verify prerequisites
  verify_prerequisites
  echo ""
  
  # Display what we're about to do
  if [[ "$DRY_RUN" == "true" ]]; then
    log_warning "DRY-RUN MODE: No changes will be applied"
    echo ""
  fi
  
  # Step 1: Apply namespace
  log_info "Step 1/7: Namespace"
  apply_manifest "00-namespace.yaml" "Namespace"
  echo ""
  
  # Step 2: Install cert-manager (unless skipped)
  if [[ "$SKIP_CERT_MANAGER" != "true" ]]; then
    log_info "Step 2/7: Cert-Manager Installation"
    apply_manifest "01-cert-manager-install.yaml" "Cert-manager"
    # All three cert-manager components must be ready before Certificate resources can be issued
    wait_for_resource "pod" "app.kubernetes.io/component=controller" "cert-manager" "300"
    wait_for_resource "pod" "app.kubernetes.io/component=cainjector" "cert-manager" "300"
    wait_for_resource "pod" "app.kubernetes.io/component=webhook" "cert-manager" "300"
    if [[ "$DRY_RUN" != "true" ]] && [[ "$SKIP_WAIT" != "true" ]]; then
      log_info "Waiting for cert-manager webhook service endpoints..."
      for _ in $(seq 1 60); do
        if kubectl get endpoints cert-manager-webhook -n cert-manager -o jsonpath='{.subsets[0].addresses[0].ip}' 2>/dev/null | grep -qE '^[0-9]'; then
          log_success "cert-manager-webhook endpoints available"
          break
        fi
        sleep 2
      done
      if ! kubectl get endpoints cert-manager-webhook -n cert-manager -o jsonpath='{.subsets[0].addresses[0].ip}' 2>/dev/null | grep -qE '^[0-9]'; then
        log_warning "cert-manager-webhook service has no endpoints yet"
        log_warning "Check: kubectl get pods -n cert-manager && kubectl describe pod -n cert-manager -l app.kubernetes.io/component=webhook"
      fi
    fi
    echo ""
  else
    log_warning "Skipping Step 2/7: Cert-Manager (assuming manual cert provisioning)"
    echo ""
  fi
  
  # Step 3: Apply CRDs
  log_info "Step 3/7: Custom Resource Definitions"
  apply_manifest "10-crds.yaml" "CRDs"
  echo ""
  
  # Step 4: Apply RBAC
  log_info "Step 4/7: RBAC and Service Accounts"
  apply_manifest "20-rbac.yaml" "RBAC"
  echo ""
  
  # Step 5: Apply services and certificates
  log_info "Step 5/7: Services and Webhook Certificates"
  apply_manifest "30-config-services.yaml" "Services"
  
  if [[ "$SKIP_CERT_MANAGER" != "true" ]]; then
    apply_manifest "02-webhook-certificate.yaml" "Webhook Certificate"
    if [[ "$DRY_RUN" != "true" ]] && [[ "$SKIP_WAIT" != "true" ]]; then
      log_info "Waiting for webhook certificate to be issued (timeout: 120s)..."
      if kubectl wait --for=condition=Ready certificate/dynatrace-webhook -n "$NAMESPACE" --timeout=120s 2>/dev/null; then
        log_success "Certificate dynatrace-webhook is Ready"
      else
        log_warning "Certificate not Ready within 120s — check: kubectl describe certificate dynatrace-webhook -n $NAMESPACE"
      fi
    fi
  fi
  echo ""
  
  # Step 6: Deploy webhook and operator
  log_info "Step 6/7: Webhook and Operator Deployment"
  apply_manifest "40-workloads-webhooks.yaml" "Webhook and Operator"
  wait_for_resource "pod" "app.kubernetes.io/component=webhook" "$NAMESPACE" "300"
  echo ""
  
  # Step 7: Create DynaKube
  log_info "Step 7/7: DynaKube Resource"
  apply_manifest "50-dynakube-oci.yaml" "DynaKube"
  echo ""
  
  # Summary
  echo "=========================================="
  if [[ "$DRY_RUN" == "true" ]]; then
    echo -e "${YELLOW}DRY-RUN COMPLETE${NC}"
    echo "No changes were applied. Run without --dry-run to deploy."
  else
    echo -e "${GREEN}DEPLOYMENT COMPLETE!${NC}"
  fi
  echo "=========================================="
  echo ""
  
  # Post-deployment instructions
  echo -e "${BLUE}Next Steps:${NC}"
  echo ""
  echo "1. Verify deployment:"
  echo "   kubectl get pods -n $NAMESPACE -l app.kubernetes.io/component=webhook"
  echo "   kubectl get dynakubes -n $NAMESPACE"
  echo ""
  echo "2. Check webhook health:"
  echo "   kubectl logs -n $NAMESPACE -l app.kubernetes.io/component=webhook -c webhook --tail=30"
  echo ""
  echo "3. Verify service endpoints:"
  echo "   kubectl get endpoints dynatrace-webhook -n $NAMESPACE"
  echo ""
  echo "4. For detailed diagnostics, see documentation:"
  echo "   - CERT-MANAGER-SETUP.md"
  echo "   - REVISIONS-BUG.md"
  echo "   - README-CERT-MANAGER.txt"
  echo ""
}

################################################################################
# Entry point
################################################################################
main "$@"
