# Signal upstream provenance

Checked on 2026-09-29. This is a pinned source investigation, not a built or working Signal fork. No upstream source tree or dependency binaries have been imported by this slice. Machine-readable pins are in [upstream-lock.json](../upstream-lock.json).

## Desktop first

The initial target is Debian 13 amd64, matching the reusable VOLPAROSSA core. The selected stable release is [Signal Desktop v8.28.0](https://github.com/signalapp/Signal-Desktop/releases/tag/v8.28.0), published 2026-09-23. Its annotated tag resolves to commit `ef3872cb0249ec939d8aff857568a0e87a6b5075`.

Its [package manifest](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/package.json) pins `@signalapp/libsignal-client` to **0.100.0**. Use the corresponding libsignal commit `857c4dca03537dc5e395a5e1eda6bf18f59c3601`, not an independently selected newer protocol library. Latest libsignal v0.103.1 was observed during this audit but is not the selected dependency.

Signal Desktop normally links to an existing Signal account. Retaining its normal account/device lifecycle is different from delivering selected encrypted device envelopes without the Signal delivery service. The initial integration does not replace registration, linking, contact lookup, group services, or authoritative device/prekey discovery.

## License and maintenance boundary

Both pinned upstreams identify their code as **AGPL-3.0-only**. Preserve their license text, file notices and applicable third-party notices; do not relabel imported Signal code as GPL-only. The upstream LICENSE bytes at both pins have SHA-256 `0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0`. The initial repository license is not permission to change upstream licensing. Signal branding and affiliation must remain distinct from this project. [Desktop package](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/package.json), [libsignal package](https://github.com/signalapp/libsignal/blob/857c4dca03537dc5e395a5e1eda6bf18f59c3601/node/package.json), [upstream license](https://github.com/signalapp/Signal-Desktop/blob/ef3872cb0249ec939d8aff857568a0e87a6b5075/LICENSE).

libsignal explicitly says external use is unsupported and its APIs may change. Pinning is an integration baseline, not a promise to freeze security updates indefinitely. Review upstream changes and retain source-build/dependency provenance before distributing a fork. No signature verification or reproducible-build claim follows merely from resolving a Git tag. [Pinned libsignal README](https://github.com/signalapp/libsignal/blob/857c4dca03537dc5e395a5e1eda6bf18f59c3601/README.md).

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
