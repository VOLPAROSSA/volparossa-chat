# Signal / VOLPAROSSA integration contract

**Status: requirements and inspected seams, not implemented functionality.** Source versions and primary references are in [UPSTREAM.md](UPSTREAM.md). The goal is to preserve normal Signal while adding authenticated VOLPAROSSA delivery between compatible clients and reciprocal storage of encrypted backups. A proxy setting cannot make an unmodified Signal client deliver messages through another messaging network.

## Keep Signal's identities and cryptography

Signal remains the owner of ACI/PNI identities, device IDs, registration IDs, safety-number/identity-change decisions, protocol sessions, prekeys and message decoding. VOLPAROSSA supplies transport and opaque storage through a narrow local interface; it must not receive Signal identity private keys, session stores, recovery keys or decrypted chat databases. Do not create a second ratchet store for the alternate transport.

At the pinned source, `OutgoingMessage.doSendMessage` encrypts separately for each device under `signalProtocolStore.enqueueSessionJob`. Its `getCiphertextMessage` uses libsignal `signalEncrypt`; `transmitUnsealedMessage` and `transmitSealedSenderMessage` then select upstream delivery. The new dispatcher belongs between these steps, retaining exact ciphertext, recipient device and registration binding. Both existing transport implementations must pass through it. [Pinned sender](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/textsecure/OutgoingMessage.preload.ts).

On receipt, extract a typed transport-independent entry into the existing encrypted receive queue. Preserve its atomic session/prekey/unprocessed-message transaction and acknowledge durable acceptance only after that commit. Continue through ordinary decryption and message processing, not a plaintext UI injection. The upstream functions include `signalDecrypt` and `signalDecryptPreKey`; no new cryptographic primitive is needed. [Pinned receiver](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/textsecure/MessageReceiver.preload.ts).

## Authenticate the device-to-peer binding

A DHT record or self-declared phone number does not prove Signal identity. The first integration must explicitly pair each compatible device through an already authenticated Signal exchange or an owner-verified out-of-band invitation. Bind the exact Signal account/device identity and registration generation to the VOLPAROSSA peer key, supported protocol version, expiry and fresh challenge response. Revoke/reconfirm that binding when identities, registrations or devices change.

Keep these bindings contact-private. Do not publish a phone-number/ACI-to-peer directory, address book, or durable messaging graph. Core peer authentication proves possession of the paired transport key; it does not replace Signal's identity trust decisions. Replays, substituted recipient devices and expired capabilities must fail closed.

Start with existing sessions and short one-to-one text messages between two paired forked desktops. Fresh sessions may still use normal Signal prekey discovery. The current key-fetch path checks signed classical and PQ prekeys and constructs `PreKeyBundle` for `processPreKeyBundle`; the session store's device list is not an independent authoritative device directory. Supporting new sessions without Signal control services later requires authenticated device-list freshness, key rotation, one-time-key allocation and revocation—not copying a cached bundle forever. [Pinned key-fetch path](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/textsecure/getKeysForServiceId.preload.ts).

## Per-device delivery, not an all-or-nothing label

| State | Required behavior |
| --- | --- |
| Authenticated compatible destination device | Send its existing Signal-protocol ciphertext through VOLPAROSSA. |
| Stock or unreachable device, normal compatibility mode | Preserve normal Signal delivery where permitted; show mixed delivery when other devices used VOLPAROSSA. |
| Strict no-Signal-delivery mode | Keep unsupported devices pending or fail explicitly; do not silently use Signal delivery. |
| Missing/invalid identity or capability | Refuse alternate delivery; availability fallback must not bypass a trust failure. |
| Ambiguous acknowledgement | Retry the same durable per-device delivery record idempotently, not a newly created user message. |

Sender synchronization also matters: `sendSyncMessage` sends the outgoing message to the sender's other devices. Therefore two modified desktops with stock linked phones do not by themselves provide whole-message server-free multi-device delivery. A strict claim must cover every addressed destination and synchronization device, plus receipts. Partial delivery must remain visible. Registration/linking/key lookup dependencies remain separately disclosed. [Pinned synchronization path](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/textsecure/SendMessage.preload.ts).

Use a durable, bounded per-device outbox and transport delivery ID tied to the exact ciphertext and original message identity. Preserve it across reconnects and permitted fallback; do not re-encrypt independently for racing transport attempts. Receiver deduplication must survive restart and both transport paths. An ACK means durable acceptance, not that the person read the message. Keep read receipts, disappearing-message rules, blocking and existing message-level deduplication intact.

Do not invent Signal server GUIDs, server delivery receipts, or trusted server timestamps for a peer transport. Add explicit transport provenance. In particular, the existing sealed-sender validator checks its certificate at `serverTimestamp`; direct delivery needs an explicit, reviewed local freshness rule, not a peer-supplied pretend server time or disabled certificate validation. Initial scope may reject unsupported envelope types while retaining normal Signal behavior outside the direct-only path. [Envelope schema](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/protos/SignalService.proto), [certificate validation](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/textsecure/MessageReceiver.preload.ts).

Attachments, group/sender-key delivery, calls, stories, offline custody and delivery to other operating systems are later slices, not features implied by a passing direct-text exchange. In particular, a direct text envelope does not prove attachments avoided Signal's CDN.

## Reciprocal encrypted backups

Reuse Signal's `local-encrypted` exporter. Its local snapshot includes `main`, `metadata`, a referenced-files list and encrypted media under the shared `SignalBackups/files` tree. Export completion must precede publishing a complete snapshot. On restore, reconstruct the exact bounded tree and run existing structure, cryptographic and importer validation; do not restore a raw live profile/database as a second active device. The pinned Desktop importer stages a backup before linking. [Backup service](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/services/backups/index.preload.ts), [local layout](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/services/backups/util/localBackup.node.ts).

The core stores bounded chunks of ciphertext, never plaintext messages or recovery keys. Snapshot metadata that exposes names, account association or attachment relationships also needs a private authenticated wrapper. No public-cache publication, public AI training or cross-user plaintext deduplication is authorized by backup consent. Recovery credentials remain owner-held and exportable through an explicit recovery flow; without them, replicated ciphertext is not a recoverable backup.

The agreed accounting unit is **physical network storage, including replicas**, not logical backup size. For example, 10 GiB of charged encrypted data stored in two complete copies requires 20 GiB of reciprocal local contribution; any additionally charged metadata/overhead is included, not hidden. The participant must provide at least that much usable storage service for others. An offer is not evidence that replicas exist: reserve capacity before admission and distinguish promised, written, verified and expired bytes. Replica count, repair traffic, retention and free-space limits must stay visible and bounded. Do not silently lower redundancy to fit a logical-size quota.

Replication must survive loss of one selected provider when the configured replica policy promises it. Partial uploads are not complete backups; missing chunks or damaged manifests must fail restoration visibly. Honor owner bandwidth/CPU/disk limits. Deletion and expiry must be propagated and verified where possible, without claiming cryptographic erasure of copies held by untrusted peers. Reciprocal storage supplies availability; it does not reproduce the hosted Signal backup service's additional TEE-based forward secrecy. [Signal's storage/security distinction](https://signal.org/blog/backup-improvements/).

## Smallest executable milestones

1. **Paired direct text:** two real forked Desktop instances, existing Signal sessions and an authenticated private peer binding; send, decrypt, persist and reply over the real core transport with Signal delivery blocked for those exact device envelopes. Also prove ordinary Signal remains available to an unmodified contact.
2. **Device lifecycle and retries:** loss of the direct ACK, restart, duplicate arrival, stale registration and identity change. Demonstrate mixed versus strict mode, including sender-device synchronization, without silent loss or duplicate UI messages.
3. **Reciprocal backup:** produce a real encrypted Signal snapshot, distribute charged replicas through core custody, remove the source copy in a disposable test, lose one provider, reconstruct and restore. Verify the recovery-key boundary and actual physical-byte accounting.

Likely fork changes are the sender dispatcher, receive adapter, binding/outbox persistence, delivery-status UI and backup service bridge. Core changes are reusable authenticated private message transport/custody APIs and storage accounting, not Signal-specific cryptography. These interfaces still need implementation; neither a source audit nor a simulated envelope exchange completes a milestone.
