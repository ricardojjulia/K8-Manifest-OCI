import argparse
import json
from pathlib import Path

# Use repository-relative defaults so regeneration works from any machine.
base_dir = Path(__file__).resolve().parent


def kind_of(doc: str) -> str:
    for ln in doc.splitlines():
        if ln.startswith('kind:'):
            return ln.split(':', 1)[1].strip()
    return ''


BUCKET_FILE_ORDER = [
    '00-namespace.yaml',
    '10-crds.yaml',
    '20-rbac.yaml',
    '30-config-services.yaml',
    '40-workloads-webhooks.yaml',
]

OCI_FINAL_FILE = '50-dynakube-oci.yaml'
OCI_REPORT_FILE = 'oci-filter-report.txt'
OCI_REPORT_JSON_FILE = 'oci-filter-report.json'

EXPLICIT_BUCKET_KINDS = {
    '00-namespace.yaml': {'Namespace'},
    '10-crds.yaml': {'CustomResourceDefinition'},
    '20-rbac.yaml': {'ClusterRole', 'ClusterRoleBinding', 'Role', 'RoleBinding', 'ServiceAccount'},
    '30-config-services.yaml': {'ConfigMap', 'Secret', 'Service'},
    # Everything listed here is expected to land in the fallback bucket.
    '40-workloads-webhooks.yaml': {
        'Deployment',
        'MutatingWebhookConfiguration',
        'PodDisruptionBudget',
        'ValidatingWebhookConfiguration',
    },
}

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Split Dynatrace operator bundle into staged manifests and helper scripts.'
    )
    parser.add_argument(
        '--src',
        default=str(base_dir / 'dynatrace-operator-bundle.yaml'),
        help='Path to the combined bundle YAML file.',
    )
    parser.add_argument(
        '--out-dir',
        default=str(base_dir / 'staged'),
        help='Output directory for staged files and helper scripts.',
    )
    parser.add_argument(
        '--strict',
        action='store_true',
        help='Fail if empty docs, missing kind, or unknown kinds are detected.',
    )
    process_group = parser.add_mutually_exclusive_group()
    process_group.add_argument(
        '-process4OCI',
        '--process4oci',
        action='store_true',
        help='Filter the operator bundle to OCI-relevant resources and append the DynaKube manifest.',
    )
    process_group.add_argument(
        '--process4classic',
        action='store_true',
        help='OCI mode shortcut that forces classicFullStack filtering semantics.',
    )
    process_group.add_argument(
        '--process4cloudnative',
        action='store_true',
        help='OCI mode shortcut that forces cloudNativeFullStack filtering semantics.',
    )
    parser.add_argument(
        '--dynakube-file',
        default=str(base_dir.parent / 'dynakube_OCI.yaml'),
        help='Path to the Dynakube OCI manifest used by --process4oci.',
    )
    parser.add_argument(
        '--report-json',
        action='store_true',
        help='When OCI processing is enabled, also write a machine-readable JSON filter report.',
    )
    parser.add_argument(
        '--fail-on-drop',
        action='store_true',
        help='In OCI modes, fail if any bundle resources are excluded by the filter.',
    )
    return parser.parse_args()


def split_documents(raw: str) -> tuple[list[str], list[int]]:
    parts: list[str] = []
    cur: list[str] = []
    empty_doc_lines: list[int] = []
    prev_was_separator = False
    seen_nonempty_doc = False

    for line_no, line in enumerate(raw.splitlines(), start=1):
        if line.strip() == '---':
            if cur:
                parts.append('\n'.join(cur).rstrip() + '\n')
                cur = []
                seen_nonempty_doc = True
            elif prev_was_separator and seen_nonempty_doc:
                # Consecutive separators after at least one real doc imply an empty doc.
                empty_doc_lines.append(line_no)
            prev_was_separator = True
        else:
            cur.append(line)
            prev_was_separator = False

    if cur:
        parts.append('\n'.join(cur).rstrip() + '\n')
    return parts, empty_doc_lines


def make_buckets(order: list[str]) -> dict[str, list[str]]:
    return {name: [] for name in order}


def all_known_kinds() -> set[str]:
    kinds: set[str] = set()
    for bucket_kinds in EXPLICIT_BUCKET_KINDS.values():
        kinds |= bucket_kinds
    return kinds


def classify_kind(kind: str) -> str:
    if kind == 'Namespace':
        return '00-namespace.yaml'
    if kind == 'CustomResourceDefinition':
        return '10-crds.yaml'
    if kind in {'ClusterRole', 'ClusterRoleBinding', 'Role', 'RoleBinding', 'ServiceAccount'}:
        return '20-rbac.yaml'
    if kind in {'ConfigMap', 'Secret', 'Service'}:
        return '30-config-services.yaml'
    return '40-workloads-webhooks.yaml'


def metadata_value(doc: str, key: str) -> str:
    in_metadata = False
    for line in doc.splitlines():
        if line == 'metadata:':
            in_metadata = True
            continue
        if in_metadata:
            if not line.startswith('  '):
                break
            prefix = f'  {key}:'
            if line.startswith(prefix):
                return line.split(':', 1)[1].strip()
    return ''


def resource_id(doc: str) -> tuple[str, str]:
    return kind_of(doc), metadata_value(doc, 'name')


def extract_capabilities(dynakube_doc: str) -> set[str]:
    capabilities: set[str] = set()
    in_activegate = False
    in_capabilities = False

    for line in dynakube_doc.splitlines():
        if line.startswith('  activeGate:'):
            in_activegate = True
            in_capabilities = False
            continue
        if in_activegate and line.startswith('  ') and not line.startswith('    '):
            in_activegate = False
            in_capabilities = False
        if not in_activegate:
            continue
        if line.startswith('    capabilities:'):
            in_capabilities = True
            continue
        if in_capabilities and line.startswith('      - '):
            capabilities.add(line.split('-', 1)[1].strip())
            continue
        if in_capabilities and line.strip() and not line.startswith('      - '):
            in_capabilities = False

    return capabilities


def dynakube_features(dynakube_doc: str) -> dict[str, object]:
    lines = dynakube_doc.splitlines()
    has_log_monitoring = any(line.startswith('  logMonitoring:') for line in lines)
    has_activegate = any(line.startswith('  activeGate:') for line in lines)
    oneagent_modes: set[str] = set()
    if any(line.startswith('    classicFullStack:') for line in lines):
        oneagent_modes.add('classicFullStack')
    if any(line.startswith('    cloudNativeFullStack:') for line in lines):
        oneagent_modes.add('cloudNativeFullStack')
    if any(line.startswith('    applicationMonitoring:') for line in lines):
        oneagent_modes.add('applicationMonitoring')

    return {
        'has_log_monitoring': has_log_monitoring,
        'has_activegate': has_activegate,
        'oneagent_modes': oneagent_modes,
        'activegate_capabilities': extract_capabilities(dynakube_doc),
    }


def apply_profile_overrides(features: dict[str, object], profile: str | None) -> dict[str, object]:
    updated = {
        'has_log_monitoring': features['has_log_monitoring'],
        'has_activegate': features['has_activegate'],
        'oneagent_modes': set(features['oneagent_modes']),
        'activegate_capabilities': set(features['activegate_capabilities']),
    }

    if profile == 'classic':
        updated['oneagent_modes'] = {'classicFullStack'}
    elif profile == 'cloudnative':
        updated['oneagent_modes'] = {'cloudNativeFullStack'}

    return updated


def load_oci_inputs(dynakube_file: Path) -> tuple[dict[str, object], list[str]]:
    if not dynakube_file.exists():
        raise FileNotFoundError(f'Dynakube OCI file not found: {dynakube_file}')

    docs, empty_doc_lines = split_documents(dynakube_file.read_text())
    if empty_doc_lines:
        shown = ', '.join(str(i) for i in empty_doc_lines[:10])
        raise ValueError(f'Dynakube OCI file contains empty YAML document(s) near separator line(s): {shown}')

    dynakube_docs = [doc for doc in docs if kind_of(doc) == 'DynaKube']
    if not dynakube_docs:
        raise ValueError(f'No DynaKube resource found in OCI file: {dynakube_file}')

    oci_docs = [doc for doc in docs if kind_of(doc) in {'Secret', 'DynaKube'}]
    if not oci_docs:
        raise ValueError(f'No OCI resources to append from: {dynakube_file}')

    return dynakube_features(dynakube_docs[0]), oci_docs


def oci_required_resources(features: dict[str, object]) -> set[tuple[str, str]]:
    required = {
        ('Namespace', 'dynatrace'),
        ('CustomResourceDefinition', 'dynakubes.dynatrace.com'),
        ('ServiceAccount', 'dynatrace-operator'),
        ('ServiceAccount', 'dynatrace-webhook'),
        ('ClusterRole', 'dynatrace-operator'),
        ('ClusterRole', 'dynatrace-webhook'),
        ('ClusterRoleBinding', 'dynatrace-operator'),
        ('ClusterRoleBinding', 'dynatrace-webhook'),
        ('Role', 'dynatrace-operator'),
        ('Role', 'dynatrace-operator-supportability'),
        ('Role', 'dynatrace-webhook'),
        ('RoleBinding', 'dynatrace-operator'),
        ('RoleBinding', 'dynatrace-operator-supportability'),
        ('RoleBinding', 'dynatrace-webhook'),
        ('Service', 'dynatrace-webhook'),
        ('Deployment', 'dynatrace-operator'),
        ('Deployment', 'dynatrace-webhook'),
        ('PodDisruptionBudget', 'dynatrace-webhook'),
        ('MutatingWebhookConfiguration', 'dynatrace-webhook'),
        ('ValidatingWebhookConfiguration', 'dynatrace-webhook'),
    }

    if features['has_activegate']:
        required.add(('ServiceAccount', 'dynatrace-activegate'))

    activegate_capabilities = features['activegate_capabilities']
    if 'kubernetes-monitoring' in activegate_capabilities:
        required.update(
            {
                ('ClusterRole', 'dynatrace-kubernetes-monitoring-default'),
                ('ClusterRole', 'dynatrace-kubernetes-monitoring'),
                ('ClusterRoleBinding', 'dynatrace-kubernetes-monitoring'),
            }
        )
    if 'kspm' in activegate_capabilities:
        required.update(
            {
                ('ServiceAccount', 'dynatrace-node-config-collector'),
                ('ClusterRole', 'dynatrace-kubernetes-monitoring-kspm'),
            }
        )

    if features['has_log_monitoring']:
        required.update(
            {
                ('ServiceAccount', 'dynatrace-logmonitoring'),
                ('ClusterRole', 'dynatrace-logmonitoring'),
                ('ClusterRoleBinding', 'dynatrace-logmonitoring'),
            }
        )

    oneagent_modes = features['oneagent_modes']
    if oneagent_modes:
        required.add(('ServiceAccount', 'dynatrace-dynakube-oneagent'))
    if features['has_log_monitoring'] and ({'classicFullStack', 'cloudNativeFullStack'} & oneagent_modes):
        required.add(('ClusterRoleBinding', 'dynatrace-logmonitoring-fullstack'))

    return required


def write_generated_outputs(
    out_dir: Path,
    order: list[str],
    buckets: dict[str, list[str]],
) -> None:
    for file_name, docs in buckets.items():
        target = out_dir / file_name
        if docs:
            target.write_text('\n---\n'.join(docs).rstrip() + '\n')
        else:
            target.write_text('')

    apply_sh = out_dir / 'apply-staged.sh'
    apply_sh.write_text(
        '#!/usr/bin/env bash\n'
        'set -euo pipefail\n'
        'DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        f'for f in {" ".join(order)}; do\n'
        '  echo "Applying $f"\n'
        '  kubectl apply -f "$DIR/$f"\n'
        'done\n'
    )
    apply_sh.chmod(0o755)

    rollback_sh = out_dir / 'rollback-staged.sh'
    rollback_order = list(reversed(order))
    rollback_sh.write_text(
        '#!/usr/bin/env bash\n'
        'set -euo pipefail\n'
        'DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        f'for f in {" ".join(rollback_order)}; do\n'
        '  echo "Deleting $f"\n'
        '  kubectl delete -f "$DIR/$f" --ignore-not-found=true\n'
        'done\n'
    )
    rollback_sh.chmod(0o755)

    stale_oci_file = out_dir / OCI_FINAL_FILE
    if OCI_FINAL_FILE not in order and stale_oci_file.exists():
        stale_oci_file.unlink()
    stale_report_file = out_dir / OCI_REPORT_FILE
    if OCI_FINAL_FILE not in order and stale_report_file.exists():
        stale_report_file.unlink()
    stale_report_json_file = out_dir / OCI_REPORT_JSON_FILE
    if OCI_FINAL_FILE not in order and stale_report_json_file.exists():
        stale_report_json_file.unlink()

    for file_name in order:
        content = (out_dir / file_name).read_text()
        count = content.count('\n---\n') + (1 if content.strip() else 0)
        print(f'{file_name}: {count} docs')
    print(f'Wrote {apply_sh}')
    print(f'Wrote {rollback_sh}')


def format_resource_ids(resource_ids: list[tuple[str, str]]) -> str:
    if not resource_ids:
        return '  (none)\n'
    return ''.join(f'  - {kind}/{name}\n' for kind, name in resource_ids)


def write_oci_report(
    out_dir: Path,
    dynakube_file: Path,
    profile: str,
    features: dict[str, object],
    kept_resource_ids: list[tuple[str, str]],
    dropped_resource_ids: list[tuple[str, str]],
) -> None:
    report_path = out_dir / OCI_REPORT_FILE
    oneagent_modes = sorted(features['oneagent_modes'])
    activegate_capabilities = sorted(features['activegate_capabilities'])

    report = (
        'OCI Filter Report\n'
        '=================\n\n'
        f'DynaKube source: {dynakube_file}\n\n'
        f'Selected profile: {profile}\n\n'
        'Detected features\n'
        '-----------------\n'
        f"- logMonitoring: {'enabled' if features['has_log_monitoring'] else 'disabled'}\n"
        f"- activeGate: {'enabled' if features['has_activegate'] else 'disabled'}\n"
        f"- oneAgent modes: {', '.join(oneagent_modes) if oneagent_modes else '(none)'}\n"
        f"- activeGate capabilities: {', '.join(activegate_capabilities) if activegate_capabilities else '(none)'}\n\n"
        f'Kept bundle resources: {len(kept_resource_ids)}\n'
        f'Dropped bundle resources: {len(dropped_resource_ids)}\n\n'
        'Kept resources\n'
        '--------------\n'
        f'{format_resource_ids(kept_resource_ids)}\n'
        'Dropped resources\n'
        '-----------------\n'
        f'{format_resource_ids(dropped_resource_ids)}'
    )
    report_path.write_text(report)
    print(f'Wrote {report_path}')


def write_oci_report_json(
    out_dir: Path,
    dynakube_file: Path,
    profile: str,
    features: dict[str, object],
    kept_resource_ids: list[tuple[str, str]],
    dropped_resource_ids: list[tuple[str, str]],
) -> None:
    report_path = out_dir / OCI_REPORT_JSON_FILE
    payload = {
        'dynakubeSource': str(dynakube_file),
        'selectedProfile': profile,
        'detectedFeatures': {
            'logMonitoring': features['has_log_monitoring'],
            'activeGate': features['has_activegate'],
            'oneAgentModes': sorted(features['oneagent_modes']),
            'activeGateCapabilities': sorted(features['activegate_capabilities']),
        },
        'keptBundleResourceCount': len(kept_resource_ids),
        'droppedBundleResourceCount': len(dropped_resource_ids),
        'keptResources': [
            {'kind': kind, 'name': name} for kind, name in kept_resource_ids
        ],
        'droppedResources': [
            {'kind': kind, 'name': name} for kind, name in dropped_resource_ids
        ],
    }
    report_path.write_text(json.dumps(payload, indent=2) + '\n')
    print(f'Wrote {report_path}')


def main() -> None:
    args = parse_args()
    src = Path(args.src).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    order = list(BUCKET_FILE_ORDER)
    oci_mode = args.process4oci or args.process4classic or args.process4cloudnative
    oci_profile: str | None = None
    if args.process4classic:
        oci_profile = 'classic'
    elif args.process4cloudnative:
        oci_profile = 'cloudnative'

    if not src.exists():
        raise FileNotFoundError(f'Source bundle not found: {src}')

    if args.report_json and not oci_mode:
        raise ValueError('--report-json requires --process4oci, --process4classic, or --process4cloudnative')
    if args.fail_on_drop and not oci_mode:
        raise ValueError('--fail-on-drop requires --process4oci, --process4classic, or --process4cloudnative')

    out_dir.mkdir(parents=True, exist_ok=True)
    parts, empty_doc_lines = split_documents(src.read_text())
    buckets = make_buckets(order)
    known_kinds = all_known_kinds()
    missing_kind_docs: list[int] = []
    unknown_kinds: set[str] = set()

    oci_features: dict[str, object] | None = None
    oci_docs: list[str] = []
    oci_required: set[tuple[str, str]] = set()
    found_resource_ids: set[tuple[str, str]] = set()
    kept_resource_ids: list[tuple[str, str]] = []
    dropped_resource_ids: list[tuple[str, str]] = []
    dynakube_file: Path | None = None

    if oci_mode:
        dynakube_file = Path(args.dynakube_file).expanduser().resolve()
        oci_features, oci_docs = load_oci_inputs(dynakube_file)
        oci_features = apply_profile_overrides(oci_features, oci_profile)
        oci_required = oci_required_resources(oci_features)
        order.append(OCI_FINAL_FILE)
        buckets = make_buckets(order)

    for idx, d in enumerate(parts, start=1):
        kind = kind_of(d)
        if not kind:
            missing_kind_docs.append(idx)
            continue

        if kind not in known_kinds:
            unknown_kinds.add(kind)

        doc_resource_id = resource_id(d)
        if oci_mode and doc_resource_id not in oci_required:
            dropped_resource_ids.append(doc_resource_id)
            continue

        found_resource_ids.add(doc_resource_id)
        if oci_mode:
            kept_resource_ids.append(doc_resource_id)
        target_bucket = classify_kind(kind)
        buckets[target_bucket].append(d)

    if oci_mode:
        missing_oci_resources = sorted(oci_required - found_resource_ids)
        if missing_oci_resources:
            details = ', '.join(f'{kind}/{name}' for kind, name in missing_oci_resources)
            raise ValueError(f'OCI processing expected bundle resources that were not found: {details}')
        if args.fail_on_drop and dropped_resource_ids:
            details = ', '.join(
                f'{kind}/{name}' for kind, name in sorted(set(dropped_resource_ids))
            )
            raise ValueError(
                'OCI processing excluded resources while --fail-on-drop is set: '
                + details
            )
        buckets[OCI_FINAL_FILE].extend(oci_docs)

    if args.strict:
        errors: list[str] = []
        if empty_doc_lines:
            shown = ', '.join(str(i) for i in empty_doc_lines[:10])
            suffix = '...' if len(empty_doc_lines) > 10 else ''
            errors.append(
                f'Found {len(empty_doc_lines)} empty YAML document(s) near separator line(s): {shown}{suffix}'
            )
        if missing_kind_docs:
            shown = ', '.join(str(i) for i in missing_kind_docs[:10])
            suffix = '...' if len(missing_kind_docs) > 10 else ''
            errors.append(f'Missing kind in document index(es): {shown}{suffix}')
        if unknown_kinds:
            errors.append(
                'Unknown kind(s) encountered: '
                + ', '.join(sorted(unknown_kinds))
                + '. Update EXPLICIT_BUCKET_KINDS if expected.'
            )
        if errors:
            raise ValueError('Strict validation failed:\n- ' + '\n- '.join(errors))

    write_generated_outputs(out_dir, order, buckets)
    if oci_mode and oci_features is not None and dynakube_file is not None:
        write_oci_report(
            out_dir,
            dynakube_file,
            oci_profile or 'dynakube-driven',
            oci_features,
            sorted(kept_resource_ids),
            sorted(dropped_resource_ids),
        )
        if args.report_json:
            write_oci_report_json(
                out_dir,
                dynakube_file,
                oci_profile or 'dynakube-driven',
                oci_features,
                sorted(kept_resource_ids),
                sorted(dropped_resource_ids),
            )


if __name__ == '__main__':
    main()
