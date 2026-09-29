# Project VOLPAROSSA Chat

A Signal-based client for **VOLPAROSSA — the Decentralized Intelligent Cooperative Network**.

Keep the familiar Signal experience, with an additional decentralized delivery path and private, cooperative backup storage supplied by the reusable [VOLPAROSSA core](https://github.com/VOLPAROSSA/volparossa).

## Development status

Integration is starting with Signal Desktop on Linux. This repository does **not yet contain a working modified Signal client**. The source baseline, integration boundaries and backup requirements are being established before changing the actual encrypted send/receive path. Existing core messages are not already Signal messages.

## Messages: Signal encryption, another delivery path

Compatible clients should exchange **Signal Protocol ciphertext** over VOLPAROSSA, without using Signal's message-delivery servers for that delivery. Signal retains responsibility for identities, sessions, encryption and decryption; the core transports opaque envelopes. Other peers must not receive plaintext, Signal identity keys or session state.

Ordinary Signal use remains available. Routing must account for every recipient device and the sender's linked-device synchronization, not just the visible desktop. The UI must distinguish fully decentralized delivery, mixed delivery and ordinary Signal delivery. Initial account registration, device discovery and prekey acquisition are separate dependencies; direct message transport does not by itself remove them.

Interrupted or ambiguous delivery must not silently create a second message or consume a second ratchet state. Delivery acknowledgements, retries, attachments, receipts, groups and offline recipients need explicit integration, not plaintext injection into the conversation UI.

## Backups: private, distributed and reciprocal

Backup storage belongs in the core, so applications beyond Signal can use it. Signal's encrypted backup archive is split into opaque chunks, stored on other participants and reconstructed for the authorized owner. It is **private retained storage**, separate from the public content cache and agent-training data. Storage peers do not receive recovery keys.

The agreed contribution rule counts **actual remotely stored bytes, including recovery copies and their overhead**. A participant using 1 GB of remote storage must make at least 1 GB available for other users; a 1 GB archive with two full copies requires about 2 GB plus overhead. Offered capacity is not unlimited free disk, and a self-reported number is not proof that remote storage exists.

Private leases, durable capacity accounting, restart recovery, renewal, expiry, deletion and complete backup restore must work before this is presented as cloud backup. Replication mitigates unavailable peers; it is not a promise of permanent availability. Chunk checksums are not encryption or authorization, and removing a lease cannot prove that an untrusted peer erased its bytes.

## Architecture and upstream

- [Signal integration contract](docs/SIGNAL_INTEGRATION.md): actual send/receive seams, compatibility and remaining work.
- [Upstream provenance](docs/UPSTREAM.md): pinned Signal Desktop/libsignal versions and licenses.
- [Core](https://github.com/VOLPAROSSA/volparossa): network transport, private storage and application interfaces.
- [Browser](https://github.com/VOLPAROSSA/volparossa-browser): a separate client of that same core.

Signal and libsignal retain their upstream AGPL-3.0-only licenses and notices; this repository's existing license does not relicense them. Project VOLPAROSSA Chat is an independent integration, not an official Signal product.
