"""Term tables that drive signal extraction.

Every table maps ``family -> (weight, [terms])``.

A term ending in ``*`` matches the stem plus any word characters, so
``predict*`` also matches ``prediction``, ``predictive`` and ``predicted``.
Terms containing a space also tolerate ``_``, ``-`` and ``.`` between the words,
so ``start of frame`` matches ``start_of_frame`` and ``startOfFrame`` once the
scanner has split identifiers into words.

These tables are deliberately camera-domain specific. Generic software words are
kept out of the mechanism table on purpose, because the critic relies on the
mechanism table being narrow enough that a hit actually means something.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Domain topics. A candidate is anchored to exactly one primary topic.
# --------------------------------------------------------------------------
DOMAIN: dict[str, tuple[float, list[str]]] = {
    "camera-hal": (
        3.0,
        [
            "camera hal", "camerahal", "hal3", "camera3", "camera device session",
            "camera_module", "camera provider", "icameradevice", "hal interface",
            "camera_metadata", "request template", "vendor tag",
        ],
    ),
    "camera-framework": (
        2.6,
        [
            "camera framework", "camera service", "camera manager", "pipeline handler",
            "camera2", "capture session", "stream configuration", "camera configuration",
            "request queue", "camera stack",
        ],
    ),
    "camera-driver": (
        2.6,
        [
            "v4l2", "videobuf2", "vb2_", "media controller", "media device", "subdev",
            "sensor driver", "kernel driver", "device tree", "i2c transfer",
        ],
    ),
    "isp": (
        3.0,
        [
            "isp", "image signal processor", "demosaic*", "debayer", "denoise", "denoising",
            "tone map", "tonemap*", "lens shading", "lsc", "black level", "colour matrix",
            "color matrix", "ccm", "gamma curve", "sharpen*", "pisp", "ipa", "bayer",
            "defective pixel", "dpc", "chromatic aberration",
        ],
    ),
    "3a": (
        3.2,
        [
            "3a", "auto exposure", "autoexposure", "awb", "auto white balance",
            "auto focus", "autofocus", "agc", "aec", "metering", "convergence",
            "exposure control", "white balance", "focus control", "lens position",
        ],
    ),
    "multi-camera": (
        3.2,
        [
            "multi camera", "multi-camera", "multicamera", "dual camera", "logical camera",
            "physical camera", "stereo pair", "camera fusion", "cross camera", "camera array",
        ],
    ),
    "sensor-control": (
        3.0,
        [
            "sensor mode", "exposure time", "analogue gain", "analog gain", "digital gain",
            "vblank", "hblank", "line length", "frame length", "binning", "sensor crop",
            "sensor control", "sensor configuration", "rolling shutter", "global shutter",
        ],
    ),
    "sensor-sync": (
        3.6,
        [
            "synchroniz*", "synchronis*", "frame sync", "start of frame", "sof",
            "vsync", "phase align*", "lockstep", "lock step", "genlock", "ptp",
            "timestamp align*", "clock drift", "trigger signal", "sync master",
            "sync slave", "frame duration alignment",
        ],
    ),
    "camera-pipeline": (
        2.8,
        [
            "camera pipeline", "capture pipeline", "processing pipeline", "pipeline stage",
            "capture request", "request completion", "frame pipeline", "pipeline depth",
        ],
    ),
    "scheduling": (
        3.0,
        [
            "schedul*", "deadline", "queue depth", "backpressure", "back pressure",
            "throttl*", "pacing", "time budget", "dispatch*", "work queue", "priority inversion",
        ],
    ),
    "memory-buffer": (
        3.0,
        [
            "dmabuf", "dma buf", "buffer pool", "bufferpool", "zero copy", "zero-copy",
            "heap allocat*", "buffer allocat*", "buffer recycl*", "memory pool",
            "cache flush", "cache invalidat*", "refcount*", "buffer fence", "mmap",
            "scatter gather", "buffer queue",
        ],
    ),
    "performance": (
        2.6,
        [
            "latency", "throughput", "frame rate", "framerate", "fps", "jitter",
            "cpu load", "cpu usage", "power consumption", "thermal", "overhead",
            "frame time", "startup time", "time to first frame",
        ],
    ),
    "image-quality": (
        2.8,
        [
            "image quality", "signal to noise", "snr", "psnr", "ssim", "sharpness",
            "colour accuracy", "color accuracy", "noise reduction", "tuning file",
            "tuning parameter", "quality metric",
        ],
    ),
    "computational-photography": (
        3.4,
        [
            "hdr", "high dynamic range", "multi frame", "multi-frame", "burst capture",
            "super resolution", "night mode", "bokeh", "depth map", "image stacking",
            "exposure bracket*", "temporal fusion", "frame fusion",
        ],
    ),
    "ai-camera": (
        3.2,
        [
            "neural network", "inference engine", "tflite", "tensorflow lite", "npu",
            "ml model", "segmentation mask", "object detection", "scene classification",
            "on device inference", "quantized model",
        ],
    ),
    "debug-profiling": (
        2.2,
        [
            "perfetto", "systrace", "atrace", "tracepoint", "profil*", "instrument*",
            "telemetry", "metric collection", "debug overlay", "frame trace",
        ],
    ),
}

# --------------------------------------------------------------------------
# Problem signals. These are the symptoms the code or history complains about.
# --------------------------------------------------------------------------
PROBLEM: dict[str, tuple[float, list[str]]] = {
    "timing": (
        3.2,
        [
            "jitter", "drift", "skew", "latency spike", "deadline miss*", "missed deadline",
            "out of order", "stall*", "timeout", "race condition", "timing violation",
            "late frame", "frame delay", "slow path",
        ],
    ),
    "resource": (
        3.2,
        [
            "starvation", "starved", "exhaust*", "memory leak", "fragmentation",
            "underrun", "overflow", "out of memory", "contention", "deadlock", "livelock",
            "buffer shortage", "allocation failure",
        ],
    ),
    "correctness": (
        3.0,
        [
            "mismatch*", "inconsistent", "incorrect", "corrupt*", "desync*", "misalign*",
            "regression", "flicker*", "tearing", "frame drop", "dropped frame",
            "lost frame", "duplicate frame", "stale data", "wrong order",
        ],
    ),
    "quality-defect": (
        2.6,
        [
            "artifact*", "banding", "ghosting", "motion blur", "colour shift", "color shift",
            "overexpos*", "underexpos*", "visible seam", "halo effect",
        ],
    ),
    "scalability": (
        2.8,
        [
            "bottleneck", "does not scale", "doesn't scale", "too slow", "expensive operation",
            "hot path", "quadratic", "cpu bound", "io bound", "blocking call",
        ],
    ),
    "known-limitation": (
        2.4,
        [
            "workaround", "work around", "hack", "fixme", "limitation", "unsupported",
            "hard coded", "hard-coded", "hardcoded", "fragile", "brittle", "not ideal",
            "temporary fix", "known issue",
        ],
    ),
}

# --------------------------------------------------------------------------
# Mechanism signals. A candidate needs at least one of these to exist.
# The table stays narrow on purpose: a hit must imply a non-trivial technique.
# --------------------------------------------------------------------------
MECHANISM: dict[str, tuple[float, list[str]]] = {
    "adaptive-control": (
        4.0,
        [
            "adaptive", "adaptively", "closed loop", "closed-loop", "feedback loop",
            "feedback control", "hysteresis", "damping factor", "controller gain",
            "pid controller", "self tuning", "auto adjust*", "dynamic adjustment",
        ],
    ),
    "prediction": (
        4.2,
        [
            "predict*", "estimator", "estimation model", "extrapolat*", "forecast*",
            "anticipat*", "kalman", "model based", "look ahead", "lookahead",
            "trend analysis", "projected value",
        ],
    ),
    "arbitration": (
        3.8,
        [
            "arbitrat*", "negotiat*", "prioritis*", "prioritiz*", "quota", "fair share",
            "fairness", "weighted allocation", "allocation policy", "contention resolution",
            "bidding", "admission control",
        ],
    ),
    "scheduling-mechanism": (
        3.6,
        [
            "deadline aware", "deadline-aware", "earliest deadline", "pacing algorithm",
            "coalesc*", "batching strategy", "deferred execution", "speculative execution",
            "prefetch*", "pipelined execution", "work stealing", "rate limit*",
        ],
    ),
    "buffer-mechanism": (
        3.6,
        [
            "buffer recycling", "watermark", "high water mark", "low water mark",
            "ring buffer", "double buffer*", "triple buffer*", "lazy allocation",
            "slab", "arena allocator", "reference counted buffer", "fence signal*",
            "deferred release",
        ],
    ),
    "synchronization-mechanism": (
        4.2,
        [
            "phase lock*", "drift compensat*", "clock recovery", "epoch", "sequence number",
            "monotonic clock", "barrier synchroni*", "handshake protocol", "time base",
            "offset correction", "timestamp interpolat*", "master slave sync",
        ],
    ),
    "calibration": (
        3.4,
        [
            "calibrat*", "compensat*", "correction curve", "lookup table", "interpolat*",
            "per unit tuning", "golden reference", "characteris*", "characteriz*",
        ],
    ),
    "resilience": (
        3.2,
        [
            "graceful degrad*", "fallback strategy", "fall back to", "recovery path",
            "retry policy", "guard band", "safety margin", "watchdog", "circuit breaker",
            "self heal*",
        ],
    ),
    "partitioning": (
        3.4,
        [
            "offload*", "partition*", "delegat*", "heterogeneous", "accelerator",
            "hybrid execution", "split across", "workload distribution", "hardware assisted",
        ],
    ),
    "state-machine": (
        2.8,
        [
            "state machine", "transition table", "handshake", "protocol phase",
            "finite state", "state transition",
        ],
    ),
}

# --------------------------------------------------------------------------
# Effect signals. Presence of a unit-bearing number is detected separately.
# --------------------------------------------------------------------------
EFFECT: dict[str, tuple[float, list[str]]] = {
    "reduction": (
        2.6,
        [
            "reduc*", "reduction", "decreas*", "lower*", "minimis*", "minimiz*",
            "eliminat*", "avoid*", "prevent*", "save*", "cut down",
        ],
    ),
    "improvement": (
        2.4,
        [
            "improv*", "increas*", "speed up", "speedup", "faster", "higher throughput",
            "better accuracy", "gain of", "optimis*", "optimiz*", "more stable",
        ],
    ),
    "guarantee": (
        3.0,
        [
            "guarantee*", "bounded", "deterministic", "worst case", "upper bound",
            "never exceeds", "always within",
        ],
    ),
}

# --------------------------------------------------------------------------
# Evaluation signals. These raise paper potential specifically.
# --------------------------------------------------------------------------
EVALUATION: dict[str, tuple[float, list[str]]] = {
    "experiment": (
        3.0,
        [
            "benchmark*", "baseline", "ablation", "experiment*", "dataset", "test harness",
            "measurement setup", "reproduc*", "a/b test", "comparison against",
            "evaluation metric", "ground truth", "statistical significance",
        ],
    ),
    # How camera repositories actually record evaluation: on named hardware or
    # on a named machine. Chosen from phrase counts over ipu6-camera-hal,
    # libcamera and libpisp (#4).
    #
    # Image quality metrics (PSNR, SSIM, MTF) occur zero or one time there, so
    # they are not listed. Rejected as mostly noise: "compared to" (geometry
    # comments), "profiling" (an atrace option), "time measurement" (an option
    # describing instrumentation, not an evaluation that was run) and "before
    # and after" (setup and cleanup comments; its one real use already says
    # "benchmark"). CPU usage and power consumption are already domain terms and
    # frame drops a problem term, so they are not counted twice.
    "field-validation": (
        2.4,
        ["tested on", "measured on", "measured with"],
    ),
}

# --------------------------------------------------------------------------
# Triviality markers. The critic uses these to reject noise.
# --------------------------------------------------------------------------
TRIVIALITY: dict[str, tuple[float, list[str]]] = {
    "cosmetic": (
        3.0,
        [
            "typo", "whitespace", "formatting", "clang-format", "clang format", "reformat*",
            "cosmetic", "spelling", "copyright header", "license header", "style fix",
            "lint fix", "comment only",
        ],
    ),
    "bookkeeping": (
        2.6,
        [
            "bump version", "version bump", "changelog", "gitignore", "update readme",
            "release notes", "merge branch", "cherry pick", "revert ", "sync with upstream",
            "regenerate", "update submodule",
        ],
    ),
    "mechanical": (
        2.4,
        [
            "rename", "move file", "split file", "extract method", "dead code",
            "remove unused", "add missing include", "fix build", "fix warning",
            "silence warning",
        ],
    ),
}

# Generic software design patterns. A mechanism backed only by these is not an
# invention, it is ordinary engineering.
KNOWN_PATTERNS: list[str] = [
    "singleton", "factory pattern", "abstract factory", "observer pattern", "visitor pattern",
    "decorator pattern", "adapter pattern", "facade", "builder pattern", "strategy pattern",
    "raii", "smart pointer", "shared_ptr", "unique_ptr", "pimpl", "crtp",
    "template specialization", "getter", "setter", "mutex lock", "scoped lock",
    "dependency injection", "callback registration",
]

# Platform plumbing that every camera stack has. Not novel by itself.
PLATFORM_STANDARD: list[str] = [
    "vidioc_", "v4l2_ioctl", "hidl", "aidl", "binder transaction", "cmakelists",
    "meson.build", "android.bp", "soong", "kbuild", "autotools", "pkg-config",
    "gradle", "makefile.am", "configure.ac",
]

# Files whose changes alone never constitute an invention.
CONFIG_PATH_HINTS: list[str] = [
    "cmakelists.txt", "meson.build", "makefile", "android.bp", "android.mk", "kconfig",
    ".gitignore", ".github/", "dockerfile", "requirements.txt", "package.json",
    "setup.py", "pyproject.toml", ".clang-format", ".editorconfig",
]

# Extension to tree-sitter language mapping used by the symbol extractor.
LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".py": "python",
    ".java": "java",
    ".rs": "rust",
    ".go": "go",
    ".js": "javascript",
    ".mjs": "javascript",
}

DOC_EXTENSIONS: set[str] = {".md", ".rst", ".txt", ".adoc"}

CONFIG_EXTENSIONS: set[str] = {".cmake", ".bp", ".mk", ".bazel", ".bzl", ".toml", ".ini"}
