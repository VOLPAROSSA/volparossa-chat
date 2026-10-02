// Copyright 2026 Project VOLPAROSSA contributors
// SPDX-License-Identifier: AGPL-3.0-only
// No placement/ledger here: invoke and verify the core's owner-signed fragment lifecycle.
import { dirname, isAbsolute, resolve } from 'node:path';
import { privateDirectory } from './archive.node.ts';
import { runStorageCli, validateConfig } from './replicas.node.ts';
import type { ReplicaConfig } from './replicas.node.ts';

export type FragmentOperation = 'create' | 'deposit' | 'restore' | 'progress' | 'status';
type Copy = { provider_key: string; charge: string };
type Fragment = { index: number; offset: number; ciphertext_bytes: number;
  confirmed_unexpired_copies: number; copies: Copy[] };
export type FragmentReport = {
  operation: string;
  logical_ciphertext_bytes: number;
  fragment_count: number;
  copies_per_fragment: 2;
  operation_complete?: boolean;
  read_consumes_archive: false;
  providers: { provider_key: string; physical_payload_charge_upper_bound: number }[];
  fragments: Fragment[];
  [name: string]: unknown;
};
const MAX_BYTES = 64 * 1024 ** 3;
const MAX_FRAGMENT_BYTES = 1024 ** 3;
const REPAIR_FIELDS = ['placement_authorizations', 'retained_copy_records', 'pending_retirements',
  'desired_copies_per_fragment', 'replacement_overhead_included'];
const FALSE_FLAGS = ['metadata_overhead_measured', 'current_remote_availability_proven',
  'independent_failure_domains_proven', 'network_contribution_credit', 'automatic_repair',
  'automatic_handoff', 'read_consumes_archive', 'erasure_coding'];
const OPERATIONS = new Set(['create', 'deposit', 'restore', 'progress', 'status']);
function ensure(condition: unknown): asserts condition {
  if (!condition) { throw new Error('VOLPAROSSA_BACKUP_INVALID_FRAGMENT_STORAGE'); }
}
function integer(value: unknown, min: number, max: number): value is number {
  return Number.isSafeInteger(value) && Number(value) >= min && Number(value) <= max;
}

export async function validateFragmentConfig(config: ReplicaConfig): Promise<void> {
  await validateConfig(config);
  ensure(config.providers.length >= 3 && !Object.hasOwn(config, 'copies'));
  ensure(integer(config.fragmentBytes ?? 16 * 1024 ** 2, 1, MAX_FRAGMENT_BYTES));
}

/** Raw reports stay private; validate coverage/accounting, never treat retained receipts as live proof. */
export function checkedFragmentReport(
  value: unknown, operation: FragmentOperation, config: ReplicaConfig, bytes: number
): FragmentReport {
  const report = value as FragmentReport;
  const original = config.providers.map(provider => provider.key);
  ensure(original.length >= 3 && original.length <= 8 && new Set(original).size === original.length);
  ensure(report && report.operation === `private_storage_fragments_${operation}`
    && integer(bytes, original.length, MAX_BYTES) && report.logical_ciphertext_bytes === bytes
    && report.copies_per_fragment === 2 && integer(report.fragment_count, original.length, 256));
  const repaired = report.report_version === 2;
  ensure(!Object.hasOwn(report, 'report_version') || repaired);
  ensure(repaired || REPAIR_FIELDS.every(key => !Object.hasOwn(report, key)));
  ensure(operation !== 'create' || !repaired);
  ensure(FALSE_FLAGS.every(key => report[key] === false)
    && report.expired_copies_remain_charged === true && report.owner_signature_verified === true);
  ensure(Array.isArray(report.fragments) && report.fragments.length === report.fragment_count
    && Array.isArray(report.providers) && report.providers.length >= original.length
    && report.providers.length <= original.length + report.fragment_count * 6
    && report.distinct_provider_identities === report.providers.length);
  const providers = new Map<string, { declared: number; charge: number; records: number }>();
  for (const [index, provider] of report.providers.entries()) {
    ensure(provider && /^[a-f0-9]{64}$/.test(provider.provider_key) && !providers.has(provider.provider_key)
      && integer(provider.physical_payload_charge_upper_bound, 0, bytes)
      && (index >= original.length || provider.provider_key === original[index]));
    providers.set(provider.provider_key, { declared: provider.physical_payload_charge_upper_bound, charge: 0, records: 0 });
  }
  let offset = 0;
  let recoverable = 0;
  let redundant = 0;
  let records = 0;
  let extendedFragments = 0;
  const charges = { reserved: 0, committed: 0, uncertain: 0 };
  report.fragments.forEach((fragment, index) => {
    ensure(fragment && fragment.index === index && fragment.offset === offset
      && integer(fragment.ciphertext_bytes, 1, MAX_FRAGMENT_BYTES) && Array.isArray(fragment.copies)
      && integer(fragment.copies.length, 2, repaired ? 8 : 2)
      && integer(fragment.confirmed_unexpired_copies, 0, fragment.copies.length));
    offset += fragment.ciphertext_bytes;
    records += fragment.copies.length;
    extendedFragments += Number(fragment.copies.length > 2);
    const seen = new Set<string>();
    let committed = 0;
    fragment.copies.forEach((copy, position) => {
      ensure(copy && providers.has(copy.provider_key) && !seen.has(copy.provider_key)
        && ['unattempted', 'reserved', 'committed', 'uncertain', 'deleted'].includes(copy.charge)
        && (position >= 2 || copy.provider_key === original[(index + position) % original.length]));
      seen.add(copy.provider_key);
      const provider = providers.get(copy.provider_key)!;
      provider.records++;
      if (copy.charge === 'reserved' || copy.charge === 'committed' || copy.charge === 'uncertain') {
        charges[copy.charge] += fragment.ciphertext_bytes;
        provider.charge += fragment.ciphertext_bytes;
      }
      committed += Number(copy.charge === 'committed');
    });
    ensure(fragment.confirmed_unexpired_copies <= committed);
    recoverable += Number(fragment.confirmed_unexpired_copies > 0);
    redundant += Number(fragment.confirmed_unexpired_copies >= 2);
  });
  const total = charges.reserved + charges.committed + charges.uncertain;
  ensure(offset === bytes && total <= bytes * (repaired ? 8 : 2)
    && report.reserved_payload_bytes === charges.reserved
    && report.committed_payload_bytes === charges.committed
    && report.uncertain_payload_bytes === charges.uncertain
    && report.physical_payload_charge_upper_bound === total
    && [...providers.values()].every(provider => provider.records > 0 && provider.charge === provider.declared)
    && report.fragments_with_confirmed_unexpired_copy === recoverable
    && report.fully_redundant_from_retained_receipts === (redundant === report.fragment_count));
  if (repaired) {
    const authorizations = records - report.fragment_count * 2;
    ensure(integer(authorizations, 1, report.fragment_count * 6)
      && report.placement_authorizations === authorizations && report.retained_copy_records === records
      && report.desired_copies_per_fragment === 2 && report.replacement_overhead_included === true
      && integer(report.pending_retirements, 0, extendedFragments)
      && report.providers.length <= original.length + authorizations);
  } else { ensure(report.providers.length === original.length); }
  if (operation === 'create' || operation === 'status') {
    ensure(!Object.hasOwn(report, 'operation_complete'));
  } else { ensure(report.operation_complete === true); }
  if (operation === 'deposit') { ensure(redundant === report.fragment_count); }
  if (operation === 'restore') {
    ensure(report.restored === true && report.whole_archive_sha256_verified === true
      && recoverable === report.fragment_count);
  }
  return report;
}

export async function fragmentCommand(
  config: ReplicaConfig, operation: FragmentOperation, state: string, bytes: number,
  extra: string[] = [], signal?: AbortSignal
): Promise<FragmentReport> {
  ensure(OPERATIONS.has(operation));
  await validateFragmentConfig(config);
  await privateDirectory(dirname(state));
  ensure(isAbsolute(state) && resolve(state) === state && integer(bytes, config.providers.length, MAX_BYTES));
  signal?.throwIfAborted();
  const args = ['--control-socket', config.controlSocket, 'storage', 'fragments', operation, '--state', state];
  if (operation !== 'status') {
    args.push('--identity', config.identity, '--passphrase-file', config.passphraseFile);
  }
  args.push(...extra);
  if (operation === 'create') {
    const fragmentBytes = config.fragmentBytes ?? 16 * 1024 ** 2;
    const actualSize = Math.min(fragmentBytes, Math.floor(bytes / config.providers.length));
    ensure(actualSize > 0 && Math.ceil(bytes / actualSize) <= 256);
    for (const provider of config.providers) { args.push('--provider-key', provider.key, '--grant', provider.grant); }
    args.push('--fragment-bytes', String(fragmentBytes), '--lifetime-seconds', String(config.lifetimeSeconds));
  }
  return runStorageCli(config, args, state, 4 * 1024 ** 2,
    value => checkedFragmentReport(value, operation, config, bytes), signal);
}
