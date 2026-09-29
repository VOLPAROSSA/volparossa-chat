# Signal upstream provenance

Checked on 2026-09-29. This is a pinned source investigation, not a built or working Signal fork. No upstream source tree or dependency binaries have been imported by this slice. Machine-readable pins are in [upstream-lock.json](../upstream-lock.json).

## Desktop first

The initial target is Debian 13 amd64, matching the reusable VOLPAROSSA core. The selected stable release is [Signal Desktop v8.28.0](https://github.com/signalapp/Signal-Desktop/releases/tag/v8.28.0), published 2026-09-23. Its annotated tag resolves to commit `ef3872cb0249ec939d8aff857568a0e87a6b5075`.

Its [package manifest](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/package.json) pins `@signalapp/libsignal-client` to **0.100.0**. Use the corresponding libsignal commit `857c4dca03537dc5e395a5e1eda6bf18f59c3601`, not an independently selected newer protocol library. Latest libsignal v0.103.1 was observed during this audit but is not the selected dependency.

Signal Desktop normally links to an existing Signal account. Retaining its normal account/device lifecycle is different from delivering selected encrypted device envelopes without the Signal delivery service. The initial integration does not replace registration, linking, contact lookup, group services, or authoritative device/prekey discovery.

## License and maintenance boundary

Both pinned upstreams identify their code as **AGPL-3.0-only**. Preserve their license text, file notices and applicable third-party notices; do not relabel imported Signal code as GPL-only. The upstream LICENSE bytes at both pins have SHA-256 `0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0`. The initial repository license is not permission to change upstream licensing. Signal branding and affiliation must remain distinct from this project. [Desktop package](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/package.json), [libsignal package](https://github.com/signalapp/libsignal/blob/857c4dca03537dc5e395a5e1eda6bf18f59c3601/node/package.json), [upstream license](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/LICENSE).

libsignal explicitly says external use is unsupported and its APIs may change. Pinning is an integration baseline, not a promise to freeze security updates indefinitely. Review upstream changes and retain source-build/dependency provenance before distributing a fork. No signature verification or reproducible-build claim follows merely from resolving a Git tag. [Pinned libsignal README](https://github.com/signalapp/libsignal/blob/857c4dca03537dc5e395a5e1eda6bf18f59c3601/README.md).

## Calling dependency audit

The same Desktop manifest pins **`@signalapp/ringrtc` 2.71.0**, Signal's native voice/video library. Its upstream `v2.71.0` ref resolves directly to commit **`4c5fdb312d3c0d6b8e4091e4be5271c020f5792d`**. Do not substitute an independently selected RingRTC release or assume Desktop's Electron HTTP proxy controls its native media sockets. [Desktop dependency](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/package.json), [RingRTC tag resolution](https://api.github.com/repos/signalapp/ringrtc/git/ref/tags/v2.71.0), [Node package](https://github.com/signalapp/ringrtc/blob/4c5fdb312d3c0d6b8e4091e4be5271c020f5792d/src/node/package.json).

That RingRTC source specifies **Signal WebRTC `7871f`** in `config/version.properties`. Its annotated tag object `3eca51d5a8c223be309dbb81e073b7f312a1046b` resolves to **`3a5cb5f6ea8d223a7b05e4e22ce3f9b77a740f8a`**; the upstream API reports that tag unsigned. RingRTC's Rust lockfile separately pins `zkgroup`/`libsignal-core` through libsignal **v0.99.1**, commit **`97801d22dcf9f5bf714f7b8fa3212cdc973ae1c8`**. That transitive source is distinct from Desktop's selected `@signalapp/libsignal-client` 0.100.0; preserve the dependency graph rather than silently aligning the versions. [WebRTC selector](https://github.com/signalapp/ringrtc/blob/4c5fdb312d3c0d6b8e4091e4be5271c020f5792d/config/version.properties), [WebRTC tag object](https://api.github.com/repos/signalapp/webrtc/git/tags/3eca51d5a8c223be309dbb81e073b7f312a1046b), [RingRTC Cargo lock](https://github.com/signalapp/ringrtc/blob/4c5fdb312d3c0d6b8e4091e4be5271c020f5792d/Cargo.lock).

RingRTC identifies its source/package as **AGPL-3.0-only**; the selected WebRTC root license has BSD three-clause terms, with additional dependency notices to preserve. RingRTC's Node package normally runs `scripts/fetch-prebuild.js` during install. No installer, downloaded native artifact, source import or build was run for this audit; resolving tags does not verify a prebuilt binary or reproduce its build. Before importing/building, record these calling dependencies in the machine-readable lock and preserve their full licenses and third-party acknowledgements. The existing `upstream-lock.json` still records only the earlier Desktop/libsignal investigation. [RingRTC package/license declaration](https://github.com/signalapp/ringrtc/blob/4c5fdb312d3c0d6b8e4091e4be5271c020f5792d/src/node/package.json), [WebRTC license](https://github.com/signalapp/webrtc/blob/3a5cb5f6ea8d223a7b05e4e22ce3f9b77a740f8a/LICENSE).

### Inspected calling seams

| Concern | Exact pinned entry points |
| --- | --- |
| Desktop setup/signaling and received device identities | [`calling.preload.ts`](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/services/calling.preload.ts): `#handleOutgoingSignaling`, `handleCallingMessage`, `#handleSendCallMessageToGroup` |
| Encrypted direct/group signaling job | [`sendCallingMessage.preload.ts`](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/jobs/helpers/sendCallingMessage.preload.ts): `sendCallingMessage` |
| Native ICE/relay admission | [`Service.ts`](https://github.com/signalapp/ringrtc/blob/4c5fdb312d3c0d6b8e4091e4be5271c020f5792d/src/node/ringrtc/Service.ts): `CallSettings`, `proceed`; [`native.rs`](https://github.com/signalapp/ringrtc/blob/4c5fdb312d3c0d6b8e4091e4be5271c020f5792d/src/rust/src/native.rs): `NativeCallContext`, relayed/direct peer-connection choice |
| ICE credentials and SFU HTTP | [`WebAPI.preload.ts`](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/textsecure/WebAPI.preload.ts): `getIceServers` (`v2/calling/relays`), `makeSfuRequest` |
| One-to-one media keys | [`connection.rs`](https://github.com/signalapp/ringrtc/blob/4c5fdb312d3c0d6b8e4091e4be5271c020f5792d/src/rust/src/core/connection.rs): `negotiate_srtp_keys`, Signal-identity-bound SRTP keys |
| Group media encryption/key lifecycle | [`group_call.rs`](https://github.com/signalapp/ringrtc/blob/4c5fdb312d3c0d6b8e4091e4be5271c020f5792d/src/rust/src/core/group_call.rs): frame-crypto context, media-key exchange and rotation |
| Candidate packet attachment, not an implemented bridge | [`injectable_network.rs`](https://github.com/signalapp/ringrtc/blob/4c5fdb312d3c0d6b8e4091e4be5271c020f5792d/src/rust/src/webrtc/injectable_network.rs): `PacketSender::send_udp`, `InjectableNetwork::receive_udp`; feature declarations in [`src/rust/Cargo.toml`](https://github.com/signalapp/ringrtc/blob/4c5fdb312d3c0d6b8e4091e4be5271c020f5792d/src/rust/Cargo.toml) |

Desktop's `connectGroupCall` and `connectCallLinkCall` also depend on an SFU URL and their respective membership/authentication material. Signal's [group-calling design](https://signal.org/blog/how-to-build-encrypted-group-calls/) explains the separation between forwarding and end-to-end frame encryption. Preserving those cryptographic properties while routing packets through the overlay does not itself remove the SFU or create a decentralized calling service. See the [explicit calling contract](SIGNAL_INTEGRATION.md#voice-and-video-preserve-signal-calling-carry-the-complete-call) for pending implementation and proof requirements.

## Inspected source seams

| Concern | Pinned source |
| --- | --- |
| Per-device encryption and transport handoff | [OutgoingMessage.preload.ts](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/textsecure/OutgoingMessage.preload.ts) |
| Receive, decrypt, persist and acknowledge | [MessageReceiver.preload.ts](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/textsecure/MessageReceiver.preload.ts) |
| Signed classical/PQ prekey bundles | [getKeysForServiceId.preload.ts](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/textsecure/getKeysForServiceId.preload.ts) |
| Session serialization and device IDs | [SignalProtocolStore.preload.ts](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/SignalProtocolStore.preload.ts) |
| Sender's linked-device synchronization | [SendMessage.preload.ts](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/textsecure/SendMessage.preload.ts) |
| Envelope types and service-only fields | [SignalService.proto](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/protos/SignalService.proto) |
| Exact protocol APIs | [libsignal TypeScript interface](https://github.com/signalapp/libsignal/blob/857c4dca03537dc5e395a5e1eda6bf18f59c3601/node/ts/index.ts) |
| Encrypted backup export/import | [backups/index.preload.ts](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/services/backups/index.preload.ts) |
| Backup keys and local snapshot format | [crypto.preload.ts](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/services/backups/crypto.preload.ts), [localBackup.node.ts](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/services/backups/util/localBackup.node.ts) |

## Backup support: current announcement versus older help text

Signal's **2026-09-28** announcement describes cross-platform on-device backup creation/restoration across Android, iOS and Desktop operating systems, accompanying iOS 8.30. It also distinguishes ordinary encrypted on-device backups from the hosted service's additional rotating-key/TEE protection: generic storage does not acquire that forward-secrecy property. [Signal announcement](https://signal.org/blog/backup-improvements/).

The [Desktop backup help page](https://support.signal.org/hc/en-us/articles/10870366816410-Signal-Desktop-Backups) still said Android-only restoration during this audit. Treat that as a documentation timing conflict, not a reason to promise support on old installed versions.

The selected Desktop release predates that announcement but already contains `exportLocalBackup`, `stageLocalBackupForImport` and `importLocalBackup`, plus `local-encrypted` backup types. Availability is separately gated by `desktop.localBackups.*` remote configuration. These code paths are evidence of upstream implementation, not evidence that this fork has exercised restore or that every release/channel has the same rollout. [Pinned backup service](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/services/backups/index.preload.ts), [feature gate](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/ts/util/isLocalBackupsEnabled.dom.ts).
