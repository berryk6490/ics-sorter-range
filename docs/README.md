# Documentation guide

Start with the [repository overview](../README.md). The documents below describe different stages of the sorter. Check a document's date when comparing it with the current source or deployed system. An older successful run does not show the system's current state.

## How the sorter works

- The [network diagram](../milestones/m1-build/NETWORK_DIAGRAM.md) explains the dedicated XLe VM layout. It also contains an older diagram from before that move.
- The [XLe VM migration](design/XLE_VM_MIGRATION.md), [movement boundary](archive/XLE_VM_MOVEMENT_BOUNDARY.md), [run identity](design/RUN_IDENTITY.md), and [XLe liveness](operations/XLE_LIVENESS.md) explain where the services run and how they handle identity and failures.
- The [three-slot plant](build-history/THREE_SLOT_PLANT.md), [stateful photoeyes](design/STATEFUL_PHOTOEYES.md), [load contract](design/LOAD_CONTRACT.md), [sighting contract](design/SIGHTING_CONTRACT.md), [chute-full contract](design/CHUTE_FULL_CONTRACT.md), and [accumulation/backpressure](design/ACCUMULATION_BACKPRESSURE.md) explain package movement and PLC state.
- The [accumulation monitor](operations/ACCUMULATION_MONITOR.md) and [XLe recovery](operations/XLE_RECOVERY.md) describe checks and recovery steps. Read their conditions before using them with a running system.

## Source, deployment, and tests

- [Drives guest copies and device contract](../deploy/README.md), [SCADA source notes](../scada/README.md), and [deployment reconciliation](operations/DEPLOYMENT_RECONCILIATION.md) help distinguish repository files from files observed on guests.
- [Testing](../TESTING.md) explains host tests and identifies checks that need guests. Test files are in [`tests/`](../tests/); host CI is defined in [`.github/workflows/host-tests.yml`](../.github/workflows/host-tests.yml).
- [Contributing](../CONTRIBUTING.md) covers source changes and evidence handling.

## Earlier work and recorded evidence

- [First slice](build-history/FIRST_SLICE.md), [plant slice](build-history/PLANT_SLICE.md), [lane 2](build-history/LANE2_PLANT.md), [lane 3](build-history/LANE3_PLANT.md), and [multi-package work](build-history/MULTI_PACKAGE.md) show how the sorter was built up.
- [XLe slice](build-history/XLE_SLICE.md), [fault-2 investigation](archive/XLE_VM_FAULT2_INVESTIGATION.md), [missed-tunnel validation](build-history/MISSED_TUNNEL_VALIDATION.md), and [Phase 2A restoration](archive/PHASE2A_RESTORATION.md) record later work and its limits.
- [Network baseline](../milestones/m1-build/NETWORK_BASELINE.md) and [Milestone 1 readiness](../milestones/m1-build/MILESTONE1_READINESS.md) record earlier network and demonstration results. The [October 14 demonstration video](videos/Milestone_1_Sorter_Demonstration.webm) and [historical three-slot HMI preview](../tests/artifacts/plant-three-concurrent.png) show those runs, not a live system.

For a source or deployment question, compare the dated document with the matching code and [deployment manifest](../deploy/deployment_manifest.json). The [older network SVG](../milestones/m1-build/sorter-network.svg) shows the layout before XLe moved to its own VM.

## Milestones

- [M1 build](../milestones/m1-build/README.md): [network notes](../milestones/m1-build/NETWORK_DIAGRAM.md), [readiness and demonstration record](../milestones/m1-build/MILESTONE1_READINESS.md), and the Milestone 1 paper.
- [M2 baseline](../milestones/m2-baseline/README.md): the existing Milestone 2 paper and the [capture metadata index](../milestones/m2-baseline/EVIDENCE.md).
- [M3 attack](../milestones/m3-attack/README.md), [M4 detect](../milestones/m4-detect/README.md), and [M5 harden](../milestones/m5-harden/README.md): milestone pages for future work.

## Folder guide

- [Design](design/) explains architecture, migration, and trust decisions.
- [Build history](build-history/) traces earlier builds and includes the still-useful three-slot design reference.
- [Operations](operations/) groups procedures and dated deployment or recovery records.
- [Archive](archive/) keeps historical records, including some applicable safety and design contracts.
- [Videos](videos/) contains demonstration recordings.
- [Milestone 1](../milestones/m1-build/) holds its paper, readiness record, and network notes.
