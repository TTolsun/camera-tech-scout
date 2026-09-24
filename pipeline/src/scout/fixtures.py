"""Offline fixtures for ``--fixture`` runs.

A dry run in CI must not depend on the GitHub API, on network access or on rate
limits. This module materialises small but realistic repositories as **real git
repositories** with real commits, so a fixture run exercises the same code path
as a live run: clone handling, commit parsing, tree-sitter symbol extraction,
evidence construction, scouting, criticism and emission.

Three fixtures are provided on purpose:

* ``example-camera/sensor-sync-hal`` should yield a strong candidate.
* ``example-camera/isp-buffer-pipeline`` should yield a second candidate and share
  a technology with the first, which exercises cross-repository linking.
* ``example-camera/cam-utils`` should be **rejected** by the critic, because it
  only contains formatting changes and a tuning constant.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import log


@dataclass
class FixtureCommit:
    message: str
    files: dict[str, str]


@dataclass
class FixtureRepo:
    org: str
    name: str
    description: str
    commits: list[FixtureCommit]
    pulls: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    releases: list[dict[str, Any]]

    @property
    def full_name(self) -> str:
        return f"{self.org}/{self.name}"


_SYNC_HEADER = """// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstdint>

namespace camera {

// SensorSyncController keeps two image sensors on a shared time base.
//
// The previous implementation programmed a fixed vblank value for both sensors
// and assumed their start of frame would stay aligned. On long captures the two
// readout clocks drift apart, which produced timestamp drift of up to 3.2 ms and
// dropped frames on the slave sensor.
//
// This controller instead predicts the next start of frame from a monotonic
// clock and applies drift compensation to the vblank of the slave sensor, which
// reduces the residual offset to 0.4 ms.
class SensorSyncController {
public:
    void configure(int64_t frameDurationNs);

    // Predicts the next start of frame and returns the vblank correction that
    // should be programmed before the next exposure.
    int32_t predictNextSof(int64_t observedSofNs);

private:
    int64_t estimateDrift(int64_t observedSofNs) const;

    int64_t frameDurationNs_ = 0;
    int64_t predictedSofNs_ = 0;
    double driftSlope_ = 0.0;
};

}  // namespace camera
"""

_SYNC_SOURCE = """// SPDX-License-Identifier: Apache-2.0
#include "sensor_sync_controller.h"

namespace camera {

void SensorSyncController::configure(int64_t frameDurationNs) {
    frameDurationNs_ = frameDurationNs;
    predictedSofNs_ = 0;
    driftSlope_ = 0.0;
}

// Closed-loop drift compensation.
//
// The estimator observes the error between the predicted start of frame and the
// measured one, and feeds a fraction of that error back into the slope. The
// damping factor avoids the oscillation that an unbounded feedback loop showed
// during multi-camera capture.
int64_t SensorSyncController::estimateDrift(int64_t observedSofNs) const {
    const int64_t error = observedSofNs - predictedSofNs_;
    return static_cast<int64_t>(error * driftSlope_);
}

int32_t SensorSyncController::predictNextSof(int64_t observedSofNs) {
    const int64_t drift = estimateDrift(observedSofNs);
    predictedSofNs_ = observedSofNs + frameDurationNs_ - drift;

    // Guard band, so a single noisy measurement cannot push the slave sensor
    // outside the frame duration and cause a dropped frame.
    const int64_t correction = drift / 2;
    driftSlope_ = 0.85 * driftSlope_ + 0.15 * static_cast<double>(drift);
    return static_cast<int32_t>(correction);
}

}  // namespace camera
"""

_SYNC_DOC = """# Multi-camera synchronisation design

## Problem

Two sensors driven from independent readout clocks accumulate timestamp drift.
Measurements on the reference platform showed a jitter of 3.2 ms after twenty
minutes of continuous capture, and the slave camera reported dropped frames once
the offset exceeded the vertical blanking budget.

## Previous approach

Previously the HAL programmed a fixed vblank for both sensors at configuration
time and never revisited it. That is sufficient for a single sensor but it cannot
absorb clock drift between two sensors.

## Mechanism

The controller predicts the next start of frame from a monotonic clock, estimates
the drift with a damped feedback loop, and applies an offset correction to the
vblank of the slave sensor before the next exposure is programmed.

## Effect

On the same twenty minute capture the residual offset stays within 0.4 ms and no
dropped frames were observed.

## Evaluation

The benchmark harness in `test/sync_benchmark.cpp` records the per frame offset
so the result can be compared against the fixed vblank baseline.
"""

_SYNC_BENCH = """// SPDX-License-Identifier: Apache-2.0
// Benchmark comparing the adaptive controller against the fixed vblank baseline.
#include "sensor_sync_controller.h"

namespace camera {

// Runs the capture workload and reports the timestamp offset distribution.
// The baseline is the fixed vblank configuration used before this controller.
void runSyncBenchmark(int frames) {
    SensorSyncController controller;
    controller.configure(33333333);
    for (int i = 0; i < frames; ++i) {
        controller.predictNextSof(static_cast<int64_t>(i) * 33333333);
    }
}

}  // namespace camera
"""

_ISP_SOURCE = """// SPDX-License-Identifier: Apache-2.0
#include "buffer_pool.h"

namespace camera {

// IspBufferPool recycles dmabuf allocations between ISP passes.
//
// Allocating a buffer per request caused allocation failure under memory
// pressure and a visible latency spike on the first frame after a mode switch.
// The pool keeps a high water mark and a low water mark, recycles buffers
// instead of freeing them, and only returns memory once occupancy stays below
// the low watermark for a whole second.
class IspBufferPool {
public:
    // Adaptive pool sizing. The target occupancy is derived from the measured
    // pipeline depth rather than from a compile time constant.
    void updateWatermarks(int measuredPipelineDepth);

    Buffer *acquire();
    void release(Buffer *buffer);

private:
    int highWaterMark_ = 0;
    int lowWaterMark_ = 0;
};

void IspBufferPool::updateWatermarks(int measuredPipelineDepth) {
    highWaterMark_ = measuredPipelineDepth + 2;
    lowWaterMark_ = measuredPipelineDepth;
}

void IspBufferPool::release(Buffer *buffer) {
    recycle(buffer);
}

}  // namespace camera
"""

# Second revision of the same file. The deferred release is added here, so the
# fixture history contains a real diff rather than an empty commit.
_ISP_SOURCE_V2 = _ISP_SOURCE.replace(
    """void IspBufferPool::release(Buffer *buffer) {
    recycle(buffer);
}""",
    """// Deferred release keeps the buffer reference counted until the ISP fence is
// signalled, which prevents the use after free that the old code hit when a
// capture request was cancelled mid pass.
void IspBufferPool::release(Buffer *buffer) {
    if (buffer->refcount() > 0) {
        return;
    }
    recycle(buffer);
}""",
)

_ISP_DOC = """# ISP buffer pipeline notes

## Buffer starvation

Under memory pressure the ISP pipeline reported allocation failure and the
capture latency increased by 18 ms on the first frame after a sensor mode
change.

## Mechanism

The pool applies watermark based buffer control with adaptive sizing. The
watermarks are recomputed from the measured pipeline depth, and buffers are
recycled rather than freed.

## Effect

Peak allocation count dropped and the latency spike was eliminated in the
measured workload.
"""

_UTILS_SOURCE = """// SPDX-License-Identifier: Apache-2.0
#include "cam_utils.h"

namespace camera {

// Adaptive metering helper for auto exposure.
// The threshold is tuned by hand on the reference sensor.
int adaptiveMeteringThreshold() {
    int threshold = 96;
    return threshold;
}

}  // namespace camera
"""

# Second revision. Only the constant changes, which is exactly the parameter
# tuning pattern the critic is expected to reject.
_UTILS_SOURCE_V2 = _UTILS_SOURCE.replace(
    "int threshold = 96;",
    "int threshold = 128;",
)

_UTILS_CONFIG = """# Build configuration for the camera utility library.
# The ISP scheduling helper is compiled in when adaptive metering is enabled.
option(ENABLE_ADAPTIVE_METERING "Enable adaptive metering" ON)
"""


FIXTURE_REPOS: list[FixtureRepo] = [
    FixtureRepo(
        org="example-camera",
        name="sensor-sync-hal",
        description="Multi-camera synchronisation HAL used as an offline fixture.",
        commits=[
            FixtureCommit(
                message=(
                    "camera: add fixed vblank configuration\n\n"
                    "Programs a static vertical blanking value for both sensors at "
                    "configuration time."
                ),
                files={
                    "src/sensor_sync_controller.h": _SYNC_HEADER.replace(
                        "// This controller instead predicts", "// TODO: predicts"
                    ),
                },
            ),
            FixtureCommit(
                message=(
                    "camera: predict start of frame and compensate sensor drift\n\n"
                    "Previously the HAL programmed a fixed vblank and assumed the two "
                    "sensors would stay aligned. Long captures showed timestamp drift of "
                    "3.2 ms and dropped frames on the slave sensor.\n\n"
                    "Predict the next start of frame from a monotonic clock and apply "
                    "drift compensation to the slave vblank. This reduces the residual "
                    "offset to 0.4 ms."
                ),
                files={
                    "src/sensor_sync_controller.h": _SYNC_HEADER,
                    "src/sensor_sync_controller.cpp": _SYNC_SOURCE,
                    "docs/synchronisation.md": _SYNC_DOC,
                },
            ),
            FixtureCommit(
                message=(
                    "test: add synchronisation benchmark against the fixed vblank baseline"
                ),
                files={"test/sync_benchmark.cpp": _SYNC_BENCH},
            ),
        ],
        pulls=[
            {
                "number": 381,
                "title": "Adaptive multi-camera synchronisation",
                "body": (
                    "This replaces the fixed vblank with a predicted start of frame and "
                    "drift compensation. Measured jitter falls from 3.2 ms to 0.4 ms on "
                    "the reference platform. The benchmark compares against the previous "
                    "fixed vblank baseline."
                ),
                "updated_at": "2026-08-14T09:12:00Z",
                "created_at": "2026-08-10T11:00:00Z",
            }
        ],
        issues=[
            {
                "number": 355,
                "title": "Timestamp drift between the two sensors after long capture",
                "body": (
                    "After about twenty minutes of continuous capture the slave camera "
                    "starts dropping frames. The timestamp offset grows steadily, which "
                    "looks like clock drift rather than a scheduling problem."
                ),
                "updated_at": "2026-07-29T08:00:00Z",
                "created_at": "2026-07-28T08:00:00Z",
            }
        ],
        releases=[
            {
                "tag_name": "v1.4.0",
                "name": "v1.4.0",
                "body": (
                    "Adaptive sensor synchronisation. Residual inter-camera offset is now "
                    "bounded at 0.4 ms."
                ),
                "published_at": "2026-08-20T10:00:00Z",
            }
        ],
    ),
    FixtureRepo(
        org="example-camera",
        name="isp-buffer-pipeline",
        description="ISP buffer management used as an offline fixture.",
        commits=[
            FixtureCommit(
                message=(
                    "isp: recycle dmabuf allocations with adaptive watermarks\n\n"
                    "Instead of allocating a buffer per request, keep a pool with a high "
                    "and a low water mark. Allocation failure under memory pressure and "
                    "the 18 ms latency spike after a mode switch are both avoided."
                ),
                files={
                    "src/buffer_pool.cpp": _ISP_SOURCE,
                    "docs/buffer-pipeline.md": _ISP_DOC,
                },
            ),
            FixtureCommit(
                message=(
                    "isp: defer buffer release until the fence is signalled\n\n"
                    "Fixes a use after free when a capture request is cancelled during an "
                    "ISP pass."
                ),
                files={"src/buffer_pool.cpp": _ISP_SOURCE_V2},
            ),
        ],
        pulls=[
            {
                "number": 92,
                "title": "Adaptive ISP buffer pool sizing",
                "body": (
                    "Derives the watermarks from the measured pipeline depth rather than "
                    "from a compile time constant. Peak buffer count drops and the first "
                    "frame latency spike is eliminated."
                ),
                "updated_at": "2026-09-02T12:00:00Z",
                "created_at": "2026-09-01T12:00:00Z",
            }
        ],
        issues=[],
        releases=[],
    ),
    FixtureRepo(
        org="example-camera",
        name="cam-utils",
        description="Utility library fixture that the critic is expected to reject.",
        commits=[
            FixtureCommit(
                message=(
                    "cam: run clang-format over the adaptive auto exposure metering helper\n\n"
                    "Whitespace only, no behaviour change."
                ),
                files={"src/cam_utils.cpp": _UTILS_SOURCE, "CMakeLists.txt": _UTILS_CONFIG},
            ),
            FixtureCommit(
                message=(
                    "cam: tune the adaptive auto exposure metering threshold\n\n"
                    "Adjust the default value from 96 to 128 for the reference sensor. Pure "
                    "parameter tuning, no behaviour change elsewhere."
                ),
                files={"src/cam_utils.cpp": _UTILS_SOURCE_V2},
            ),
            FixtureCommit(
                message=(
                    "cam: bump version and update the changelog for the adaptive auto "
                    "exposure helper"
                ),
                files={"CHANGELOG.md": "# Changelog\n\n## 0.2.1\n\n- Version bump.\n"},
            ),
        ],
        pulls=[],
        issues=[],
        releases=[],
    ),
]


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def materialise(root: Path, force: bool = False) -> dict[str, Path]:
    """Create the fixture repositories on disk and return their paths."""
    paths: dict[str, Path] = {}
    for repo in FIXTURE_REPOS:
        path = root / repo.org / repo.name
        if path.exists():
            if not force:
                paths[repo.full_name] = path
                continue
            shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)
        _git(["init", "--initial-branch=main"], path)
        _git(["config", "user.email", "fixture@example.invalid"], path)
        _git(["config", "user.name", "Camera Tech Scout fixture"], path)
        _git(["config", "commit.gpgsign", "false"], path)

        for index, commit in enumerate(repo.commits):
            for rel, content in commit.files.items():
                target = path / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            _git(["add", "-A"], path)
            env_date = f"2026-0{min(9, 6 + index)}-1{index + 1}T10:00:00"
            subprocess.run(
                ["git", "commit", "-m", commit.message, "--date", env_date],
                cwd=str(path), check=True, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
        paths[repo.full_name] = path
        log.debug("materialised fixture %s at %s", repo.full_name, path)
    return paths


def github_payloads(full_name: str) -> dict[str, list[dict[str, Any]]]:
    for repo in FIXTURE_REPOS:
        if repo.full_name == full_name:
            return {"pulls": repo.pulls, "issues": repo.issues, "releases": repo.releases}
    return {"pulls": [], "issues": [], "releases": []}


def metadata(full_name: str) -> dict[str, Any]:
    for repo in FIXTURE_REPOS:
        if repo.full_name == full_name:
            return {
                "description": repo.description,
                "default_branch": "main",
                "language": "C++",
                "stargazers_count": 0,
                "pushed_at": "2026-09-20T10:00:00Z",
                "html_url": f"https://github.com/{repo.full_name}",
                "archived": False,
            }
    return {}
