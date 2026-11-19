# Signal / VOLPAROSSA integration contract

**The native backup round trip and a manually paired offline text path work in isolated integration trials.** Source versions and primary references are in [UPSTREAM.md](UPSTREAM.md); the [backup connector](BACKUP_CONNECTOR.md) records the exact passing trial and remaining limitations. The goal is to preserve normal Signal while adding authenticated VOLPAROSSA delivery between compatible clients, voice/video calling over VOLPAROSSA and reciprocal storage of encrypted backups. A proxy setting cannot make an unmodified Signal client deliver messages through another messaging network or prove that its native call media uses that proxy.

## Paired offline text on Linux

The normal composer can use a manually authenticated pairing between two
existing, single-device Signal sessions. Desktop binds the local and remote ACI,
device number, registration ID and identity public key to one signed Core
mailbox invitation in each direction. The native sender commits the ratchet and
the exact encrypted packet in one SQLCipher transaction. Retrying a pending
deposit reuses that packet. The recipient uses Signal’s normal message receiver.
A private import marker for a text requires the saved incoming row, sender,
device, timestamp and body digest to match. A native receipt requires the
corresponding outgoing rows to have reached the reported status. Core acknowledges
both providers only after the bridge sees the marker.

The pairing file and Core configuration live in the profile’s private
`volparossa-mailbox` directory, mode `0700`, in files of mode `0600`.
The paired device records in `pair.json` carry `aci`, `device`, `registration`
and `identity` (hex-encoded Signal public key). The other fields are `version`
(1), `local`, `remote`, `sendGrant`, `receiveGrant` and `expires` (Unix
milliseconds). The two grant values are SHA-256 digests of the signed invitation
files. Independently authenticate both devices and both invitation bindings
before provisioning this configuration. The native fixture derives the public
device records from its known local registration service.

`core.json` names the trusted `executable`, local control `socket`, `identity`,
`passphrase`, `sendInvitation` and `receiveInvitation` by absolute canonical
path. `remoteOwner` is the remote Core owner public key in hex. The bridge
rechecks the invitation digests. The CLI verifies the signatures and performs
the normal private deposit, fetch and confirmation operations.

```sh
python3 -B scripts/signal_mailbox.py deposit --mailbox "$SENDER_MAILBOX"
python3 -B scripts/signal_mailbox.py fetch --mailbox "$RECIPIENT_MAILBOX"
# Open the recipient's Desktop on its existing profile.
python3 -B scripts/signal_mailbox.py confirm --mailbox "$RECIPIENT_MAILBOX"
```

Run the same commands in the opposite direction for native delivery receipts.
Each poll reads the current provider inbox. A poll before native import leaves
the message available. Multiple fetches and a reopen preserve the original
message row. The sender marks a message delivered only when its normal Signal
receipt arrives. Recipient-authorized profile updates and native receipt types
use the same private channel. Each profile currently retains up to 256 unexpired
mailbox transaction records, including receipts, and each handoff queue accepts
up to 256 files. Settled handoffs remain in the private profile. Unpaired
contacts continue to use Signal. A stale
or changed pairing, a changed device set, or unsupported paired content produces
a pending or failed send.

The hosted native trial registers two separate standalone Signal Desktop 8.28.0
accounts, establishes sessions through the upstream loopback test service and
then refuses ordinary Signal submissions to both paired devices. It closes the
recipient for deposit and closes the sender for pickup. The test reopens both
native databases, exercises duplicate pickup and compares the same local
ratchet across replay. Its two real Core stores use authenticated local test
streams. Verified cross-machine routing, automatic authenticated discovery,
general multi-device fan-out, groups, calls and interactive pairing remain
open. The provider fixture and reproducible workflow are public.
[Source versions, executable hashes and native trial results](../provenance/paired-mailbox-20261119.json) ·
[Hosted trial](https://github.com/VOLPAROSSA/volparossa-chat/actions/runs/44519772345) ·
[Core fixture](https://github.com/VOLPAROSSA/volparossa/pull/429) ·
[Native workflow](../.github/workflows/native-receipts.yml).

## Keep Signal's identities and cryptography

Signal remains the owner of ACI/PNI identities, device IDs, registration IDs, safety-number/identity-change decisions, protocol sessions, prekeys and message decoding. VOLPAROSSA supplies transport and opaque storage through a narrow local interface; it must not receive Signal identity private keys, session stores, recovery keys or decrypted chat databases. Do not create a second ratchet store for the alternate transport.

At the pinned source, `OutgoingMessage.doSendMessage` encrypts separately for each device under `signalProtocolStore.enqueueSessionJob`. Its `getCiphertextMessage` uses libsignal `signalEncrypt`; `transmitUnsealedMessage` and `transmitSealedSenderMessage` then select upstream delivery. The new dispatcher belongs between these steps, retaining exact ciphertext, recipient device and registration binding. Both existing transport implementations must pass through it. [Pinned sender](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/textsecure/OutgoingMessage.preload.ts).

On receipt, extract a typed transport-independent entry into the existing encrypted receive queue. Preserve its atomic session/prekey/unprocessed-message transaction and acknowledge durable acceptance only after that commit. Continue through ordinary decryption and message processing, not a plaintext UI injection. The upstream functions include `signalDecrypt` and `signalDecryptPreKey`; no new cryptographic primitive is needed. [Pinned receiver](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/textsecure/MessageReceiver.preload.ts).

### Receipts from a linked device

The Desktop overlay handles repeated message timestamps inside one encrypted
delivery, read or viewed receipt. The normal receiver keeps the first occurrence
of each original 64-bit timestamp in order. Timestamps in separate envelopes
or from different devices still produce separate events. Signal's existing
confirmation callback and handling for unknown receipt types are preserved.

After staging the pinned source and applying the overlay, run
`node --test tests/signal-receipt-dispatch.mjs`. This executes the staged
`MessageReceiver` receipt method with synthetic envelopes and event consumers.
It checks the per-device rows and confirmation callback.

The **Native Signal receipts** workflow builds the pinned Desktop and runs
`python3 -B scripts/run_native_receipts.py`. Two synthetic contacts decrypt four
messages sent through the Desktop UI and send encrypted delivery receipts
through Signal’s local mock service. One receipt references a message sent to
the other contact. The test reopens the same profile and queries the native
database for each original row ID and recipient status. Success publishes the
counts and source hashes. All test traffic stays on loopback. Delivery through
the VOLPAROSSA Core and calling have separate acceptance criteria below.

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

Attachments, group/sender-key delivery, stories, offline custody and delivery to other operating systems are later slices, not features implied by a passing direct-text exchange. In particular, a direct text envelope does not prove attachments avoided Signal's CDN. Voice/video calls are explicitly required too, with the separate signaling and media integration below.

## Voice and video: preserve Signal calling, carry the complete call

The requested scope includes **one-to-one and group voice/video calls over VOLPAROSSA**, while preserving ordinary Signal calls with stock contacts, device/ringing behavior and existing call-link/screen-sharing compatibility. This is additional required work, not implemented functionality or a reprioritization of the current message/storage slices.

Both **signaling and actual media** need integration. Desktop queues encrypted `CallingMessage` jobs and passes received device-bound messages to RingRTC; RingRTC separately owns the native media path. Preserve its one-to-one Signal-identity-bound SRTP keys and its group frame encryption/key changes. The core transports encrypted packets, not media keys or decrypted audio/video. See the [exact pinned entry points](UPSTREAM.md#inspected-calling-seams).

Group calls also depend on a selective forwarding unit (SFU), membership/credentials and HTTP control requests. Reaching Signal's existing SFU through the overlay is not server-free group calling. A later participant-operated alternative needs its own compatible forwarding, admission and interoperability proof; no decentralized SFU deployment architecture is selected here. Signal's [group-calling design](https://signal.org/blog/how-to-build-encrypted-group-calls/) explains why forwarding and end-to-end media encryption are separate.

For an overlay-required call, constrain native sockets/packet handling **and ICE candidate gathering** to the authorized core attachment: no out-of-scope host/public-address disclosure, STUN/TURN/SFU bypass or silent ordinary-network fallback. A learned ICE address is not permission for arbitrary raw-IP egress. Signal's `hideIp`/always-relay setting alone does not prove core routing. Report signaling/media transports and remaining Signal-service dependencies separately; explicit compatibility mode must not bypass identity or policy failures.

Call media, screen content, credentials and private signaling must never enter the public cache or agent training. Keep resource limits, real-time congestion behavior and cleanup without creating a new durable recording or call graph. RingRTC's feature-gated `InjectableNetwork` is a candidate packet seam, not a ready Desktop-to-core bridge. Real packet evidence—not merely a connected-call UI—is needed before claiming that attachment works.

## Reciprocal encrypted backups

Reuse Signal's `local-encrypted` exporter. Its local snapshot includes `main`, `metadata`, a referenced-files list and encrypted media under the shared `SignalBackups/files` tree. Export completion must precede publishing a complete snapshot. On restore, reconstruct the exact bounded tree and run existing structure, cryptographic and importer validation; do not restore a raw live profile/database as a second active device. The pinned Desktop importer stages a backup before linking. [Backup service](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/services/backups/index.preload.ts), [local layout](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/services/backups/util/localBackup.node.ts).

The core stores bounded chunks of ciphertext, never plaintext messages or recovery keys. Snapshot metadata that exposes names, account association or attachment relationships also needs a private authenticated wrapper. No public-cache publication, public AI training or cross-user plaintext deduplication is authorized by backup consent. Recovery credentials remain owner-held and exportable through an explicit recovery flow; without them, replicated ciphertext is not a recoverable backup.

The agreed accounting unit is **physical network storage, including replicas**, not logical backup size. For example, 10 GiB of charged encrypted data stored in two complete copies requires 20 GiB of reciprocal local contribution; any additionally charged metadata/overhead is included, not hidden. The participant must provide at least that much usable storage service for others. An offer is not evidence that replicas exist: reserve capacity before admission and distinguish promised, written, verified and expired bytes. Replica count, repair traffic, retention and free-space limits must stay visible and bounded. Do not silently lower redundancy to fit a logical-size quota.

Contribution must follow usage down as well as up. After actual remote physical usage falls from 2 GB to 1 GB, the core should lower the local contribution target to 1 GB. It must first transfer other owners' live fragments to verified independent replacement holders and acknowledge custody before freeing their occupied space. Missing replacement capacity stays visible as pending drain; migration overhead is counted, not hidden. The core owns this adaptive placement/accounting controller, not a separate Signal-only implementation. It remains unimplemented by the present local-storage milestone.

Replication must survive loss of one selected provider when the configured replica policy promises it. Partial uploads are not complete backups; missing chunks or damaged manifests must fail restoration visibly. Honor owner bandwidth/CPU/disk limits. Deletion and expiry must be propagated and verified where possible, without claiming cryptographic erasure of copies held by untrusted peers. Reciprocal storage supplies availability; it does not reproduce the hosted Signal backup service's additional TEE-based forward secrecy. [Signal's storage/security distinction](https://signal.org/blog/backup-improvements/).

## Smallest executable milestones

1. **Paired direct text:** two real forked Desktop instances, existing Signal sessions and an authenticated private peer binding; send, decrypt, persist and reply over the real core transport with Signal delivery blocked for those exact device envelopes. Also prove ordinary Signal remains available to an unmodified contact.
2. **Device lifecycle and retries:** loss of the direct ACK, restart, duplicate arrival, stale registration and identity change. Demonstrate mixed versus strict mode, including sender-device synchronization, without silent loss or duplicate UI messages.
3. **Reciprocal backup:** produce a real encrypted Signal snapshot, distribute charged replicas through core custody, remove the source copy in a disposable test, lose one provider, reconstruct and restore. Verify the recovery-key boundary and actual physical-byte accounting.
4. **Real one-to-one calling:** two source-built pinned Desktop instances, bidirectional RingRTC audio/video over the core, call lifecycle, route-loss refusal and stock-client compatibility. Use synthetic media in a disposable topology; prove actual egress and state remaining Signal dependencies.
5. **Group calling:** at least three real participants, join/leave key lifecycle and encrypted SFU media over the core. Test cleanup and overlay-required refusal; distinguish Signal-SFU compatibility from any later verified participant-hosted alternative.

Likely fork changes are the sender dispatcher, receive adapter, binding/outbox persistence, delivery-status UI, native calling attachment and backup service bridge. Core changes are reusable authenticated private message, call-transport and custody interfaces plus storage accounting, not Signal-specific cryptography. For calling, the first vertical implementation is the native packet boundary plus a real one-to-one media exchange—not a new crypto stack or a decentralized-SFU claim. These interfaces still need implementation; neither a source audit nor a simulated envelope exchange completes a milestone.
