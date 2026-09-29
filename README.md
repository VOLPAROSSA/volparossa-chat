# Project VOLPAROSSA Chat

A Signal-based client for **VOLPAROSSA — the Decentralized Intelligent Cooperative Network**.

Keep the familiar Signal experience, with decentralized messaging, voice/video calling and private, cooperative backup storage supplied by the reusable [VOLPAROSSA core](https://github.com/VOLPAROSSA/volparossa).

## Development status

Integration is starting with Signal Desktop on Linux. This repository does **not yet contain a working modified Signal client**. A reproducible [encrypted-backup connector candidate](docs/BACKUP_CONNECTOR.md) now targets the exact upstream exporter, attachment encryption and native importer, using the core's retained replica storage. Its bounded file/process tests pass; the combined Electron, Signal-crypto and real-core test has not run. Existing core messages are not already Signal messages.

## Messages: Signal encryption, another delivery path

Compatible clients should exchange **Signal Protocol ciphertext** over VOLPAROSSA, without using Signal's message-delivery servers for that delivery. Signal retains responsibility for identities, sessions, encryption and decryption; the core transports opaque envelopes. Other peers must not receive plaintext, Signal identity keys or session state.

Ordinary Signal use remains available. Routing must account for every recipient device and the sender's linked-device synchronization, not just the visible desktop. The UI must distinguish fully decentralized delivery, mixed delivery and ordinary Signal delivery. Initial account registration, device discovery and prekey acquisition are separate dependencies; direct message transport does not by itself remove them.

Interrupted or ambiguous delivery must not silently create a second message or consume a second ratchet state. Delivery acknowledgements, retries, attachments, receipts, groups and offline recipients need explicit integration, not plaintext injection into the conversation UI.

## Calls: familiar controls, protected network paths

Voice and video calls are part of the requested integration, including one-to-one and group conversations. Both call setup and encrypted media need a VOLPAROSSA path; delivering a call invitation through the network alone is not enough. Keep Signal's existing call cryptography and ordinary calling compatibility, and distinguish fully decentralized calls from mixed or Signal-backed calls.

Call media, private signaling and contact associations are not public-cache or training inputs. Group forwarding and connection fallback require their own integration and privacy checks. This is planned functionality, not a working calling feature in this repository yet.

## Backups: private, distributed and reciprocal

Backup storage belongs in the core, so applications beyond Signal can use it. Signal's encrypted backup archive is split into opaque chunks, stored on other participants and reconstructed for the authorized owner. It is **private retained storage**, separate from the public content cache and agent-training data. Storage peers do not receive recovery keys.

The agreed contribution rule counts **actual remotely stored bytes, including recovery copies and their overhead**. A participant using 1 GB of remote storage must make at least 1 GB available for other users; a 1 GB archive with two full copies requires about 2 GB plus overhead. Offered capacity is not unlimited free disk, and a self-reported number is not proof that remote storage exists.

The contribution target follows usage down as well as up. Reducing actual remote use from 2 GB to 1 GB should reduce that target to 1 GB. Other users' live fragments must first move to verified replacement holders before their occupied space is released; insufficient replacement capacity remains a visible pending reduction, not silent data loss. This adaptive controller belongs in the core and is not implemented by the current local store.

Private leases, durable capacity accounting, restart recovery, renewal, expiry, deletion and complete backup restore must work before this is presented as cloud backup. Replication mitigates unavailable peers; it is not a promise of permanent availability. Chunk checksums are not encryption or authorization, and removing a lease cannot prove that an untrusted peer erased its bytes.

## Architecture and upstream

- [Signal integration contract](docs/SIGNAL_INTEGRATION.md): actual send/receive seams, compatibility and remaining work.
- [Executable backup candidate](docs/BACKUP_CONNECTOR.md): source staging, connector boundaries and the pending native proof.
- [Upstream provenance](docs/UPSTREAM.md): pinned Signal Desktop/libsignal versions and licenses.
- [Core](https://github.com/VOLPAROSSA/volparossa): network transport, private storage and application interfaces.
- [Browser](https://github.com/VOLPAROSSA/volparossa-browser): a separate client of that same core.

Signal and libsignal retain their upstream AGPL-3.0-only licenses and notices; this repository's existing license does not relicense them. Project VOLPAROSSA Chat is an independent integration, not an official Signal product.
