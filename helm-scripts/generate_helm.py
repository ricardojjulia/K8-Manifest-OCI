#!/usr/bin/env python3
"""
generate_helm.py — Helm-based parallel to split_manifests.py.

Reads dynakube_OCI.yaml, detects Dynatrace features, and generates:
  staged/00-cert-manager-values.yaml   — cert-manager Helm values
  staged/10-operator-values.yaml       — dynatrace-operator Helm values
  staged/20-dynakube-cr.yaml           — DynaKube CR (verbatim extract)
  staged/install.sh                    — staged helm install script
  staged/uninstall.sh                  — staged helm uninstall script
  staged/helm-values-report.txt        — human-readable feature report
  staged/helm-values-report.json       — machine-readable report (--report-json)

No code is shared with split_manifests.py — this is a fully standalone script.
"""
import argparse
import json
from pathlib import Path

base_dir = Path(__file__).resolve().parent

# ── Chart / release constants ──────────────────────────────────────────────
CERT_MANAGER_CHART = "jetstack/cert-manager"
CERT_MANAGER_RELEASE = "cert-manager"
CERT_MANAGER_NS = "cert-manager"
CERT_MANAGER_VERSION = "v1.14.5"

OPERATOR_CHART = "dynatrace/dynatrace-operator"
OPERATOR_RELEASE = "dynatrace-operator"
OPERATOR_DEFAULT_IMAGE = "public.ecr.aws/dynatrace/dynatrace-operator"
OPERATOR_DEFAULT_TAG = "v1.4.0"

REPORT_TXT = "helm-values-report.txt"
REPORT_JSON_FILE = "helm-values-report.json"

# Every feature this script knows how to map to Helm values.
# Used by --fail-on-drop to detect unmapped features.
KNOWN_FEATURES: set[str] = {
    "classicFullStack",
    "cloudNativeFullStack",
    "applicationMonitoring",
    "logMonitoring",
    "activeGate",
    "routing",
    "kubernetes-monitoring",
    "dynatrace-api",
    "kspm",
}


# ── Argument parsing ───────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Generate Helm values files and staged install/uninstall scripts "
            "from a DynaKube OCI manifest."
        )
    )
    p.add_argument(
        "--dynakube-file",
        default=str(base_dir.parent / "dynakube_OCI.yaml"),
        help="Path to the DynaKube OCI YAML file.",
    )
    p.add_argument(
        "--out-dir",
        default=str(base_dir / "staged"),
        help="Output directory for generated files.",
    )
    p.add_argument(
        "--profile",
        choices=["dynakube", "classic", "cloudnative"],
        default="dynakube",
        help=(
            "Feature profile. 'dynakube' reads from the file; "
            "'classic'/'cloudnative' override the oneAgent mode."
        ),
    )
    p.add_argument(
        "--operator-image",
        default=OPERATOR_DEFAULT_IMAGE,
        help="Operator container image repository.",
    )
    p.add_argument(
        "--operator-tag",
        default=OPERATOR_DEFAULT_TAG,
        help="Operator image tag.",
    )
    p.add_argument(
        "--operator-digest",
        default="",
        help="Optional operator image digest (sha256:...).",
    )
    p.add_argument(
        "--oci-registry",
        default="",
        help="OCI registry prefix for oneAgent and activeGate image overrides.",
    )
    p.add_argument(
        "--chart-version",
        default="",
        help="Pin the dynatrace-operator chart version in the install script.",
    )
    p.add_argument(
        "--namespace",
        default="dynatrace",
        help="Kubernetes namespace for Dynatrace resources.",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Fail if dynakube_OCI.yaml has empty YAML docs, no DynaKube resource, "
            "or no oneAgent mode is detected (when using dynakube profile)."
        ),
    )
    p.add_argument(
        "--report-json",
        action="store_true",
        help="Also write helm-values-report.json.",
    )
    p.add_argument(
        "--fail-on-drop",
        action="store_true",
        help=(
            "Fail if any detected feature has no Helm values mapping in this script. "
            "Guards against silently ignoring new features."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written; do not write files.",
    )
    return p.parse_args()


# ── YAML document splitting ────────────────────────────────────────────────

def split_documents(raw: str) -> tuple[list[str], list[int]]:
    """Split a multi-document YAML string into individual document strings."""
    docs: list[str] = []
    current: list[str] = []
    empty_lines: list[int] = []
    prev_sep = False
    seen_nonempty = False

    for lineno, line in enumerate(raw.splitlines(), start=1):
        if line.strip() == "---":
            if current:
                docs.append("\n".join(current).rstrip() + "\n")
                current = []
                seen_nonempty = True
            elif prev_sep and seen_nonempty:
                empty_lines.append(lineno)
            prev_sep = True
        else:
            current.append(line)
            prev_sep = False

    if current:
        docs.append("\n".join(current).rstrip() + "\n")
    return docs, empty_lines


# ── Minimal YAML field extraction ─────────────────────────────────────────

def kind_of(doc: str) -> str:
    """Return the 'kind:' value from a YAML document."""
    for line in doc.splitlines():
        if line.startswith("kind:"):
            return line.split(":", 1)[1].strip()
    return ""


# ── ActiveGate capabilities extraction ────────────────────────────────────

def extract_activegate_capabilities(doc: str) -> set[str]:
    """Return the set of activeGate capabilities list items from a DynaKube doc."""
    caps: set[str] = set()
    in_activegate = False
    in_capabilities = False

    for line in doc.splitlines():
        if line.startswith("  activeGate:"):
            in_activegate = True
            in_capabilities = False
            continue
        # A 2-space top-level key ends the activeGate block
        if in_activegate and line.startswith("  ") and not line.startswith("    "):
            in_activegate = False
            in_capabilities = False
        if not in_activegate:
            continue
        if line.startswith("    capabilities:"):
            in_capabilities = True
            continue
        if in_capabilities:
            if line.startswith("      - "):
                caps.add(line.split("-", 1)[1].strip())
            elif line.strip() and not line.startswith("      "):
                in_capabilities = False
    return caps


# ── Feature detection ──────────────────────────────────────────────────────

def detect_features(dynakube_doc: str) -> dict:
    """Detect Dynatrace features from a DynaKube document."""
    lines = dynakube_doc.splitlines()

    has_log_monitoring = any(
        line.startswith("  logMonitoring:") for line in lines
    )
    has_activegate = any(
        line.startswith("  activeGate:") for line in lines
    )

    oneagent_modes: set[str] = set()
    for line in lines:
        s = line.strip()
        # Only match active (non-commented) entries
        if s.startswith("classicFullStack:"):
            oneagent_modes.add("classicFullStack")
        elif s.startswith("cloudNativeFullStack:"):
            oneagent_modes.add("cloudNativeFullStack")
        elif s.startswith("applicationMonitoring:"):
            oneagent_modes.add("applicationMonitoring")

    return {
        "has_log_monitoring": has_log_monitoring,
        "has_activegate": has_activegate,
        "oneagent_modes": oneagent_modes,
        "activegate_capabilities": extract_activegate_capabilities(dynakube_doc),
    }


def apply_profile_override(features: dict, profile: str) -> dict:
    """Apply a CLI profile override to the detected features."""
    updated = {
        "has_log_monitoring": features["has_log_monitoring"],
        "has_activegate": features["has_activegate"],
        "oneagent_modes": set(features["oneagent_modes"]),
        "activegate_capabilities": set(features["activegate_capabilities"]),
    }
    if profile == "classic":
        updated["oneagent_modes"] = {"classicFullStack"}
    elif profile == "cloudnative":
        updated["oneagent_modes"] = {"cloudNativeFullStack"}
    return updated


# ── Values builders ────────────────────────────────────────────────────────

def build_certmanager_values() -> dict:
    """Static cert-manager Helm values — no feature input needed."""
    return {
        "installCRDs": True,
        "startupapicheck": {"enabled": True},
    }


def csidriver_needed(features: dict) -> bool:
    """CSI driver is required for cloudNativeFullStack and applicationMonitoring."""
    return bool({"cloudNativeFullStack", "applicationMonitoring"} & features["oneagent_modes"])


def build_operator_values(features: dict, args: argparse.Namespace) -> dict:
    """
    Build the dynatrace-operator Helm values dict from detected features.

    Chart-level toggles only. DynaKube spec fields (logMonitoring, activeGate
    capabilities, oneAgent mode config) belong in 20-dynakube-cr.yaml.
    """
    image: dict = {
        "repository": args.operator_image,
        "tag": args.operator_tag,
    }
    if args.operator_digest:
        image["digest"] = args.operator_digest

    values: dict = {
        "operator": {"enabled": True, "image": image},
        "webhook": {"enabled": True},
        "installCRDs": True,
        "namespaceName": args.namespace,
        "csidriver": {"enabled": csidriver_needed(features)},
    }

    if args.oci_registry:
        values["oneAgent"] = {"image": f"{args.oci_registry}/dynatrace-oneagent"}
        if features["has_activegate"]:
            values["activeGate"] = {"image": f"{args.oci_registry}/dynatrace-activegate"}

    return values


# ── YAML serializer (stdlib only, no PyYAML) ──────────────────────────────

def dict_to_yaml(data: dict, indent: int = 0) -> str:
    """
    Serialize a dict to YAML. Handles nested dicts, lists, bools, and strings.
    Sufficient for Helm values files — no multi-line strings or YAML anchors.
    """
    lines: list[str] = []
    prefix = "  " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(dict_to_yaml(value, indent + 1))
        elif isinstance(value, list):
            lines.append(f"{prefix}{key}:")
            for item in value:
                lines.append(f"{prefix}  - {item}")
        elif isinstance(value, bool):
            lines.append(f"{prefix}{key}: {'true' if value else 'false'}")
        elif value == "" or value is None:
            lines.append(f"{prefix}{key}: ''")
        else:
            lines.append(f"{prefix}{key}: {value}")
    return "\n".join(lines)


# ── Validation ─────────────────────────────────────────────────────────────

def validate_strict(
    empty_doc_lines: list[int],
    has_dynakube: bool,
    features: dict,
    profile: str,
) -> list[str]:
    """Return a list of validation error strings."""
    errors: list[str] = []
    if empty_doc_lines:
        shown = ", ".join(str(i) for i in empty_doc_lines[:10])
        suffix = "..." if len(empty_doc_lines) > 10 else ""
        errors.append(
            f"Found {len(empty_doc_lines)} empty YAML document(s) near "
            f"separator line(s): {shown}{suffix}"
        )
    if not has_dynakube:
        errors.append("No DynaKube resource found in dynakube_OCI.yaml.")
    if not features["oneagent_modes"] and profile == "dynakube":
        errors.append(
            "No active oneAgent mode detected (classicFullStack / "
            "cloudNativeFullStack / applicationMonitoring). "
            "Use --profile classic or --profile cloudnative to override."
        )
    return errors


def validate_fail_on_drop(features: dict) -> list[str]:
    """Return unmapped features — detected but not in KNOWN_FEATURES."""
    detected: set[str] = set()
    detected |= features["oneagent_modes"]
    detected |= features["activegate_capabilities"]
    if features["has_log_monitoring"]:
        detected.add("logMonitoring")
    if features["has_activegate"]:
        detected.add("activeGate")
    return sorted(detected - KNOWN_FEATURES)


# ── File writers ───────────────────────────────────────────────────────────

def write_yaml_file(
    path: Path,
    data: dict,
    header_comment: str,
    dry_run: bool,
) -> None:
    content = f"# {header_comment}\n{dict_to_yaml(data)}\n"
    if dry_run:
        print(f"[dry-run] Would write {path}:\n{content}")
    else:
        path.write_text(content)
    print(f"Wrote {path}")


def write_install_script(
    out_dir: Path,
    args: argparse.Namespace,
    features: dict,
    dry_run: bool,
) -> None:
    ns = args.namespace
    chart_ver_line = (
        f"  --version \"{args.chart_version}\" \\\n" if args.chart_version else ""
    )
    csi_comment = (
        "# csidriver: enabled (cloudNativeFullStack / applicationMonitoring detected)"
        if csidriver_needed(features)
        else "# csidriver: disabled (classicFullStack or no oneAgent mode)"
    )

    # Build the helm operator install command as a separate string to avoid
    # f-string brace conflicts with chart_ver_line interpolation.
    helm_operator_cmd = (
        f"helm upgrade --install {OPERATOR_RELEASE} {OPERATOR_CHART} \\\n"
        f"  --namespace \"$DT_NAMESPACE\" --create-namespace \\\n"
        f"{chart_ver_line}"
        f"  -f \"$DIR/10-operator-values.yaml\" \\\n"
        f"  $DRY_RUN_FLAG"
    )

    content = (
        "#!/usr/bin/env bash\n"
        "# Generated by generate_helm.py — do not edit by hand.\n"
        "# Installs cert-manager, dynatrace-operator, and DynaKube CR in dependency order.\n"
        "#\n"
        "# Usage:\n"
        "#   ./install.sh [--dry-run] [--skip-cert-manager] [--skip-wait]\n"
        "#                [--namespace NS] [--help]\n"
        "set -euo pipefail\n"
        "DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
        "\n"
        "# ── Configurable defaults (override via env or CLI flags) ──────────────\n"
        f"DT_NAMESPACE=\"${{DT_NAMESPACE:-{ns}}}\"\n"
        "DRY_RUN=\"${DRY_RUN:-false}\"\n"
        "SKIP_CERT_MANAGER=\"${SKIP_CERT_MANAGER:-false}\"\n"
        "SKIP_WAIT=\"${SKIP_WAIT:-false}\"\n"
        "\n"
        "# ── Argument parsing ───────────────────────────────────────────────────\n"
        "while [[ $# -gt 0 ]]; do\n"
        "  case \"$1\" in\n"
        "    --dry-run)           DRY_RUN=true; shift ;;\n"
        "    --skip-cert-manager) SKIP_CERT_MANAGER=true; shift ;;\n"
        "    --skip-wait)         SKIP_WAIT=true; shift ;;\n"
        "    --namespace)         DT_NAMESPACE=\"$2\"; shift 2 ;;\n"
        "    --help|-h)\n"
        "      echo \"Usage: $0 [--dry-run] [--skip-cert-manager] [--skip-wait] [--namespace NS]\"\n"
        "      exit 0 ;;\n"
        "    *) echo \"Unknown flag: $1\" >&2; exit 1 ;;\n"
        "  esac\n"
        "done\n"
        "\n"
        "DRY_RUN_FLAG=\"\"\n"
        "[[ \"$DRY_RUN\" == \"true\" ]] && DRY_RUN_FLAG=\"--dry-run\"\n"
        "\n"
        "GREEN='\\033[0;32m'\n"
        "BLUE='\\033[0;34m'\n"
        "YELLOW='\\033[1;33m'\n"
        "NC='\\033[0m'\n"
        "\n"
        "echo \"==========================================\"\n"
        "echo \" Dynatrace Helm Install (staged)\"\n"
        "echo \"==========================================\"\n"
        "echo \" Namespace : $DT_NAMESPACE\"\n"
        "echo \" Dry-run   : $DRY_RUN\"\n"
        "echo \"==========================================\"\n"
        "\n"
        "# ── Step 1/3: cert-manager ─────────────────────────────────────────────\n"
        "if [[ \"$SKIP_CERT_MANAGER\" == \"false\" ]]; then\n"
        f"  echo -e \"${{BLUE}}[1/3]${{NC}} Installing cert-manager...\"\n"
        "  helm repo add jetstack https://charts.jetstack.io --force-update 2>/dev/null || true\n"
        f"  helm upgrade --install {CERT_MANAGER_RELEASE} {CERT_MANAGER_CHART} \\\n"
        f"    --namespace {CERT_MANAGER_NS} --create-namespace \\\n"
        f"    --version {CERT_MANAGER_VERSION} \\\n"
        "    -f \"$DIR/00-cert-manager-values.yaml\" \\\n"
        "    $DRY_RUN_FLAG\n"
        "\n"
        "  if [[ \"$SKIP_WAIT\" == \"false\" && \"$DRY_RUN\" == \"false\" ]]; then\n"
        "    echo \"  Waiting for cert-manager controller pod...\"\n"
        "    kubectl wait --for=condition=Ready pod \\\n"
        f"      -l app.kubernetes.io/name=cert-manager \\\n"
        f"      -n {CERT_MANAGER_NS} --timeout=300s\n"
        "\n"
        "    echo \"  Waiting for cert-manager cainjector pod...\"\n"
        "    kubectl wait --for=condition=Ready pod \\\n"
        f"      -l app.kubernetes.io/name=cainjector \\\n"
        f"      -n {CERT_MANAGER_NS} --timeout=300s\n"
        "\n"
        "    echo \"  Waiting for cert-manager webhook pod...\"\n"
        "    kubectl wait --for=condition=Ready pod \\\n"
        f"      -l app.kubernetes.io/name=webhook \\\n"
        f"      -n {CERT_MANAGER_NS} --timeout=300s\n"
        "\n"
        "    # Poll until the webhook Service has a live endpoint (max 120s).\n"
        "    # This gate prevents the CA injection race that caused prior failures.\n"
        "    echo \"  Polling cert-manager-webhook endpoint (max 120s)...\"\n"
        "    DEADLINE=$(( $(date +%s) + 120 ))\n"
        "    while true; do\n"
        "      EP=$(kubectl get endpoints cert-manager-webhook \\\n"
        f"             -n {CERT_MANAGER_NS} \\\n"
        "             -o jsonpath='{.subsets[0].addresses[0].ip}' 2>/dev/null || true)\n"
        "      if [[ -n \"$EP\" ]]; then\n"
        f"        echo -e \"  ${{GREEN}}cert-manager-webhook endpoint ready: $EP${{NC}}\"\n"
        "        break\n"
        "      fi\n"
        "      if [[ $(date +%s) -ge $DEADLINE ]]; then\n"
        f"        echo -e \"  ${{YELLOW}}ERROR: cert-manager-webhook endpoint not ready after 120s.${{NC}}\" >&2\n"
        "        exit 1\n"
        "      fi\n"
        "      sleep 5\n"
        "    done\n"
        "\n"
        "    # Poll until cert-manager cainjector has injected its own caBundle into the\n"
        "    # cert-manager ValidatingWebhookConfiguration. Without this gate, applying\n"
        "    # cert-manager Certificate/Issuer resources fails with:\n"
        "    #   x509: certificate signed by unknown authority\n"
        "    # This must complete before any cert-manager resources are applied.\n"
        "    echo \"  Waiting for cainjector to inject caBundle into cert-manager webhook (max 120s)...\"\n"
        "    DEADLINE=$(( $(date +%s) + 120 ))\n"
        "    while true; do\n"
        "      CABUNDLE=$(kubectl get validatingwebhookconfiguration cert-manager-webhook \\\n"
        "        -o jsonpath='{.webhooks[0].clientConfig.caBundle}' 2>/dev/null || true)\n"
        "      if [[ -n \"$CABUNDLE\" ]]; then\n"
        f"        echo -e \"  ${{GREEN}}cert-manager webhook caBundle injected${{NC}}\"\n"
        "        break\n"
        "      fi\n"
        "      if [[ $(date +%s) -ge $DEADLINE ]]; then\n"
        "        echo \"ERROR: cert-manager webhook caBundle not injected after 120s.\" >&2\n"
        "        echo \"       Check: kubectl describe validatingwebhookconfiguration cert-manager-webhook\" >&2\n"
        "        exit 1\n"
        "      fi\n"
        "      sleep 3\n"
        "    done\n"
        "\n"
        "    # Restart the cert-manager controller so its informer does a fresh List\n"
        "    # against the API server after all resources are in place.\n"
        "    echo \"  Restarting cert-manager controller to sync informer cache...\"\n"
        f"    kubectl rollout restart deployment/cert-manager -n {CERT_MANAGER_NS}\n"
        f"    kubectl rollout status deployment/cert-manager -n {CERT_MANAGER_NS} --timeout=60s\n"
        "  fi\n"
        f"  echo -e \"${{GREEN}}✓ cert-manager installed${{NC}}\"\n"
        "else\n"
        f"  echo -e \"${{YELLOW}}[1/3] Skipping cert-manager (--skip-cert-manager)${{NC}}\"\n"
        "fi\n"
        "\n"
        "# ── Step 2/3: dynatrace-operator ───────────────────────────────────────\n"
        f"echo -e \"${{BLUE}}[2/3]${{NC}} Installing dynatrace-operator...\"\n"
        "helm repo add dynatrace \\\n"
        "  https://raw.githubusercontent.com/Dynatrace/dynatrace-operator/main/config/helm/repos/stable \\\n"
        "  --force-update 2>/dev/null || true\n"
        f"{csi_comment}\n"
        f"{helm_operator_cmd}\n"
        "\n"
        "if [[ \"$SKIP_WAIT\" == \"false\" && \"$DRY_RUN\" == \"false\" ]]; then\n"
        "  echo \"  Waiting for dynatrace-operator pod...\"\n"
        "  kubectl wait --for=condition=Ready pod \\\n"
        "    -l app.kubernetes.io/name=dynatrace-operator \\\n"
        "    -n \"$DT_NAMESPACE\" --timeout=300s || true\n"
        "\n"
        "  echo \"  Waiting for dynatrace-webhook pod...\"\n"
        "  kubectl wait --for=condition=Ready pod \\\n"
        "    -l app.kubernetes.io/component=webhook \\\n"
        "    -n \"$DT_NAMESPACE\" --timeout=300s\n"
        "fi\n"
        f"echo -e \"${{GREEN}}✓ dynatrace-operator installed${{NC}}\"\n"
        "\n"
        "# ── Step 3/3: DynaKube CR ─────────────────────────────────────────────\n"
        "# Wait for cainjector to populate the DynaKube CRD conversion webhook caBundle.\n"
        "# Without this, applying the DynaKube CR fails with x509: certificate signed by\n"
        "# unknown authority because the API server cannot verify the conversion webhook.\n"
        "if [[ \"$DRY_RUN\" == \"false\" ]]; then\n"
        "  echo \"  Waiting for cainjector to inject caBundle into DynaKube and EdgeConnect CRDs (max 120s)...\"\n"
        "  DEADLINE=$(( $(date +%s) + 120 ))\n"
        "  while true; do\n"
        "    DK_BUNDLE=$(kubectl get crd dynakubes.dynatrace.com \\\n"
        "      -o jsonpath='{.spec.conversion.webhook.clientConfig.caBundle}' 2>/dev/null || true)\n"
        "    EC_BUNDLE=$(kubectl get crd edgeconnects.dynatrace.com \\\n"
        "      -o jsonpath='{.spec.conversion.webhook.clientConfig.caBundle}' 2>/dev/null || true)\n"
        "    if [[ -n \"$DK_BUNDLE\" && -n \"$EC_BUNDLE\" ]]; then\n"
        f"      echo -e \"  ${{GREEN}}caBundle injected into DynaKube and EdgeConnect CRDs${{NC}}\"\n"
        "      break\n"
        "    fi\n"
        "    if [[ $(date +%s) -ge $DEADLINE ]]; then\n"
        "      echo \"ERROR: caBundle not injected into CRDs after 120s.\" >&2\n"
        "      echo \"       DynaKube: $([ -n \\\"$DK_BUNDLE\\\" ] && echo injected || echo missing)\" >&2\n"
        "      echo \"       EdgeConnect: $([ -n \\\"$EC_BUNDLE\\\" ] && echo injected || echo missing)\" >&2\n"
        "      exit 1\n"
        "    fi\n"
        "    sleep 3\n"
        "  done\n"
        "fi\n"
        "\n"
        "# The DynaKube CR is applied via kubectl — it is not managed by Helm.\n"
        "# This is intentional: the CR is authored in dynakube_OCI.yaml and\n"
        "# extracted verbatim; there is no benefit to wrapping it in Helm.\n"
        f"echo -e \"${{BLUE}}[3/3]${{NC}} Applying DynaKube CR...\"\n"
        "if [[ \"$DRY_RUN\" == \"true\" ]]; then\n"
        "  kubectl diff -f \"$DIR/20-dynakube-cr.yaml\" || true\n"
        "else\n"
        "  kubectl apply -f \"$DIR/20-dynakube-cr.yaml\"\n"
        "fi\n"
        f"echo -e \"${{GREEN}}✓ DynaKube CR applied${{NC}}\"\n"
        "\n"
        "echo \"\"\n"
        "echo \"==========================================\"\n"
        "echo \" Deployment Complete!\"\n"
        "echo \"==========================================\"\n"
        "echo \"\"\n"
        "echo \"Check status:\"\n"
        f"echo \"  kubectl get pods -n {CERT_MANAGER_NS}\"\n"
        "echo \"  kubectl get pods -n $DT_NAMESPACE\"\n"
        "echo \"  kubectl get dynakubes -n $DT_NAMESPACE\"\n"
        "echo \"\"\n"
        "echo \"View logs:\"\n"
        "echo \"  kubectl logs -n $DT_NAMESPACE -l app.kubernetes.io/component=webhook\"\n"
        "echo \"\"\n"
    )

    path = out_dir / "install.sh"
    if not dry_run:
        path.write_text(content)
        path.chmod(0o755)
    print(f"Wrote {path}")


def write_uninstall_script(
    out_dir: Path,
    args: argparse.Namespace,
    dry_run: bool,
) -> None:
    ns = args.namespace
    content = (
        "#!/usr/bin/env bash\n"
        "# Generated by generate_helm.py — do not edit by hand.\n"
        "# Uninstalls all staged releases in reverse dependency order.\n"
        "#\n"
        "# Usage:\n"
        "#   ./uninstall.sh [--purge-namespaces] [--namespace NS] [--help]\n"
        "set -euo pipefail\n"
        "DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
        "\n"
        f"DT_NAMESPACE=\"${{DT_NAMESPACE:-{ns}}}\"\n"
        "PURGE_NS=\"${PURGE_NS:-false}\"\n"
        "\n"
        "while [[ $# -gt 0 ]]; do\n"
        "  case \"$1\" in\n"
        "    --purge-namespaces) PURGE_NS=true; shift ;;\n"
        "    --namespace)        DT_NAMESPACE=\"$2\"; shift 2 ;;\n"
        "    --help|-h)\n"
        "      echo \"Usage: $0 [--purge-namespaces] [--namespace NS]\"\n"
        "      exit 0 ;;\n"
        "    *) echo \"Unknown flag: $1\" >&2; exit 1 ;;\n"
        "  esac\n"
        "done\n"
        "\n"
        "GREEN='\\033[0;32m'\n"
        "BLUE='\\033[0;34m'\n"
        "NC='\\033[0m'\n"
        "\n"
        "echo \"==========================================\"\n"
        "echo \" Dynatrace Helm Uninstall (staged)\"\n"
        "echo \"==========================================\"\n"
        "\n"
        "# Step 1/3: DynaKube CR — must be removed FIRST.\n"
        "# The operator webhook validates CR deletions. If the webhook pod is\n"
        "# removed first, the API server cannot reach the admission endpoint\n"
        "# and the delete request will be blocked.\n"
        f"echo -e \"${{BLUE}}[1/3]${{NC}} Deleting DynaKube CR...\"\n"
        "kubectl delete -f \"$DIR/20-dynakube-cr.yaml\" --ignore-not-found=true\n"
        f"echo -e \"${{GREEN}}✓ DynaKube CR removed${{NC}}\"\n"
        "\n"
        "# Step 2/3: dynatrace-operator\n"
        f"echo -e \"${{BLUE}}[2/3]${{NC}} Uninstalling dynatrace-operator...\"\n"
        f"helm uninstall {OPERATOR_RELEASE} --namespace \"$DT_NAMESPACE\" --ignore-not-found 2>/dev/null || true\n"
        "if [[ \"$PURGE_NS\" == \"true\" ]]; then\n"
        "  kubectl delete namespace \"$DT_NAMESPACE\" --ignore-not-found=true\n"
        "fi\n"
        f"echo -e \"${{GREEN}}✓ dynatrace-operator removed${{NC}}\"\n"
        "\n"
        "# Step 3/3: cert-manager\n"
        f"echo -e \"${{BLUE}}[3/3]${{NC}} Uninstalling cert-manager...\"\n"
        f"helm uninstall {CERT_MANAGER_RELEASE} --namespace {CERT_MANAGER_NS} --ignore-not-found 2>/dev/null || true\n"
        "if [[ \"$PURGE_NS\" == \"true\" ]]; then\n"
        f"  kubectl delete namespace {CERT_MANAGER_NS} --ignore-not-found=true\n"
        "fi\n"
        f"echo -e \"${{GREEN}}✓ cert-manager removed${{NC}}\"\n"
        "\n"
        "echo \"\"\n"
        "echo \"==========================================\"\n"
        "echo \" Uninstall Complete!\"\n"
        "echo \"==========================================\"\n"
        "echo \"\"\n"
    )

    path = out_dir / "uninstall.sh"
    if not dry_run:
        path.write_text(content)
        path.chmod(0o755)
    print(f"Wrote {path}")


# ── Report helpers ─────────────────────────────────────────────────────────

def _feature_lines(features: dict) -> list[str]:
    modes = sorted(features["oneagent_modes"])
    caps = sorted(features["activegate_capabilities"])
    return [
        f"  logMonitoring   : {'enabled' if features['has_log_monitoring'] else 'disabled'}",
        f"  activeGate      : {'enabled' if features['has_activegate'] else 'disabled'}",
        f"  oneAgent modes  : {', '.join(modes) if modes else '(none)'}",
        f"  activeGate caps : {', '.join(caps) if caps else '(none)'}",
    ]


def _values_lines(features: dict, args: argparse.Namespace) -> list[str]:
    lines = [
        "  10-operator-values.yaml:",
        "    operator.enabled          : true",
        "    webhook.enabled           : true",
        "    installCRDs               : true",
        f"    csidriver.enabled         : {str(csidriver_needed(features)).lower()}",
        f"    operator.image.repository : {args.operator_image}",
        f"    operator.image.tag        : {args.operator_tag}",
    ]
    if args.operator_digest:
        lines.append(f"    operator.image.digest     : {args.operator_digest}")
    if args.oci_registry:
        lines.append(f"    oneAgent.image            : {args.oci_registry}/dynatrace-oneagent")
        if features["has_activegate"]:
            lines.append(f"    activeGate.image          : {args.oci_registry}/dynatrace-activegate")
    return lines


def write_report_txt(
    out_dir: Path,
    dynakube_file: Path,
    profile: str,
    features: dict,
    args: argparse.Namespace,
    unmapped: list[str],
    dry_run: bool,
) -> None:
    gen_files = [
        "  staged/00-cert-manager-values.yaml",
        "  staged/10-operator-values.yaml",
        "  staged/20-dynakube-cr.yaml",
        "  staged/install.sh",
        "  staged/uninstall.sh",
        "  staged/helm-values-report.txt",
    ]
    if args.report_json:
        gen_files.append("  staged/helm-values-report.json")

    body = "\n".join([
        "Helm Values Report",
        "==================",
        "",
        f"DynaKube source : {dynakube_file}",
        f"Selected profile: {profile}",
        "",
        "Detected features",
        "-----------------",
        *_feature_lines(features),
        "",
        "Generated Helm values",
        "---------------------",
        *_values_lines(features, args),
        "",
        f"Unmapped features: {', '.join(unmapped) if unmapped else '(none)'}",
        "",
        "Generated files",
        "---------------",
        *gen_files,
        "",
    ])
    path = out_dir / REPORT_TXT
    if not dry_run:
        path.write_text(body)
    print(f"Wrote {path}")


def write_report_json(
    out_dir: Path,
    dynakube_file: Path,
    profile: str,
    features: dict,
    args: argparse.Namespace,
    unmapped: list[str],
    dry_run: bool,
) -> None:
    helm_values: dict = {
        "operator.enabled": True,
        "webhook.enabled": True,
        "installCRDs": True,
        "csidriver.enabled": csidriver_needed(features),
        "operator.image.repository": args.operator_image,
        "operator.image.tag": args.operator_tag,
    }
    if args.operator_digest:
        helm_values["operator.image.digest"] = args.operator_digest
    if args.oci_registry:
        helm_values["oneAgent.image"] = f"{args.oci_registry}/dynatrace-oneagent"
        if features["has_activegate"]:
            helm_values["activeGate.image"] = f"{args.oci_registry}/dynatrace-activegate"

    gen_files = [
        "staged/00-cert-manager-values.yaml",
        "staged/10-operator-values.yaml",
        "staged/20-dynakube-cr.yaml",
        "staged/install.sh",
        "staged/uninstall.sh",
        "staged/helm-values-report.txt",
    ]
    if args.report_json:
        gen_files.append("staged/helm-values-report.json")

    payload = {
        "dynakubeSource": str(dynakube_file),
        "selectedProfile": profile,
        "detectedFeatures": {
            "logMonitoring": features["has_log_monitoring"],
            "activeGate": features["has_activegate"],
            "oneAgentModes": sorted(features["oneagent_modes"]),
            "activeGateCapabilities": sorted(features["activegate_capabilities"]),
        },
        "generatedHelmValues": helm_values,
        "unmappedFeatures": unmapped,
        "generatedFiles": gen_files,
    }

    path = out_dir / REPORT_JSON_FILE
    if not dry_run:
        path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {path}")


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    dynakube_file = Path(args.dynakube_file).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    if not dynakube_file.exists():
        raise FileNotFoundError(f"DynaKube file not found: {dynakube_file}")

    docs, empty_doc_lines = split_documents(dynakube_file.read_text())
    dynakube_docs = [d for d in docs if kind_of(d) == "DynaKube"]
    cr_docs = [d for d in docs if kind_of(d) in {"DynaKube", "Secret"}]

    has_dynakube = bool(dynakube_docs)

    features: dict = (
        detect_features(dynakube_docs[0])
        if dynakube_docs
        else {
            "has_log_monitoring": False,
            "has_activegate": False,
            "oneagent_modes": set(),
            "activegate_capabilities": set(),
        }
    )
    features = apply_profile_override(features, args.profile)
    profile_label = "dynakube-driven" if args.profile == "dynakube" else args.profile

    # ── Validation ─────────────────────────────────────────────────────────
    if args.strict:
        errors = validate_strict(empty_doc_lines, has_dynakube, features, args.profile)
        if errors:
            raise ValueError("Strict validation failed:\n- " + "\n- ".join(errors))

    unmapped = validate_fail_on_drop(features) if args.fail_on_drop else []
    if args.fail_on_drop and unmapped:
        raise ValueError(
            "--fail-on-drop: detected features with no Helm values mapping: "
            + ", ".join(unmapped)
        )

    # ── Write outputs ───────────────────────────────────────────────────────
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    write_yaml_file(
        out_dir / "00-cert-manager-values.yaml",
        build_certmanager_values(),
        "Generated by generate_helm.py — cert-manager Helm values",
        args.dry_run,
    )

    write_yaml_file(
        out_dir / "10-operator-values.yaml",
        build_operator_values(features, args),
        f"Generated by generate_helm.py — dynatrace-operator Helm values "
        f"[profile: {profile_label}]",
        args.dry_run,
    )

    # DynaKube CR: verbatim extract of DynaKube + Secret docs from dynakube_OCI.yaml
    cr_path = out_dir / "20-dynakube-cr.yaml"
    cr_content = ("---\n" + "\n---\n".join(cr_docs).rstrip() + "\n") if cr_docs else ""
    if not args.dry_run:
        cr_path.write_text(cr_content)
    print(f"Wrote {cr_path}")

    write_install_script(out_dir, args, features, args.dry_run)
    write_uninstall_script(out_dir, args, args.dry_run)
    write_report_txt(out_dir, dynakube_file, profile_label, features, args, unmapped, args.dry_run)

    if args.report_json:
        write_report_json(out_dir, dynakube_file, profile_label, features, args, unmapped, args.dry_run)

    # ── Summary ─────────────────────────────────────────────────────────────
    modes = sorted(features["oneagent_modes"])
    caps = sorted(features["activegate_capabilities"])
    print(f"\nProfile    : {profile_label}")
    print(f"oneAgent   : {', '.join(modes) or '(none)'}")
    print(f"activeGate : {'yes' if features['has_activegate'] else 'no'}"
          f" ({', '.join(caps) or 'no caps'})")
    print(f"logMonitor : {'yes' if features['has_log_monitoring'] else 'no'}")
    print(f"csidriver  : {'enabled' if csidriver_needed(features) else 'disabled'}")


if __name__ == "__main__":
    main()
