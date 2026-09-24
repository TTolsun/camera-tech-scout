"""Display labels for generated Korean prose.

Lexicon terms are stored as stems so that ``predict*`` also matches
``prediction``. Stems read badly in a headline, so every stem that can reach a
title or a sentence has a Korean display form here.

Two conventions are applied throughout:

* Established technical terms keep their original spelling (``Camera HAL``,
  ``ISP``, ``dmabuf``, ``deadline``). Translating them would make the text
  harder to match against the source repositories, not easier.
* Every phrase is shaped so that the caller can append a Korean noun such as
  ``기법`` or ``문제`` before a particle. That keeps 을/를 and 이/가 agreement
  correct without a morphological analyser.
"""

from __future__ import annotations

DOMAIN_LABEL: dict[str, str] = {
    "camera-hal": "Camera HAL",
    "camera-framework": "Camera Framework",
    "camera-driver": "Camera Driver 계층",
    "isp": "ISP 처리",
    "3a": "3A 제어",
    "multi-camera": "Multi-camera 동작",
    "sensor-control": "Sensor 제어",
    "sensor-sync": "Sensor 동기화",
    "camera-pipeline": "Camera Pipeline",
    "scheduling": "Pipeline 스케줄링",
    "memory-buffer": "Buffer 및 메모리 관리",
    "performance": "카메라 성능",
    "image-quality": "화질",
    "computational-photography": "Computational Photography",
    "ai-camera": "AI 카메라 처리",
    "debug-profiling": "디버깅 및 프로파일링",
}

PROBLEM_LABEL: dict[str, str] = {
    "timing": "타이밍 편차",
    "resource": "자원 고갈",
    "correctness": "프레임 정합성 결함",
    "quality-defect": "화질 결함",
    "scalability": "확장성 한계",
    "known-limitation": "작성자가 직접 기록한 한계",
}

MECHANISM_LABEL: dict[str, str] = {
    "adaptive-control": "적응 제어",
    "prediction": "예측 기반 추정",
    "arbitration": "자원 중재",
    "scheduling-mechanism": "Deadline 인식 스케줄링",
    "buffer-mechanism": "Buffer 수명 제어",
    "synchronization-mechanism": "타이밍 동기화",
    "calibration": "캘리브레이션과 보정",
    "resilience": "점진적 성능 저하 대응",
    "partitioning": "작업 분할",
    "state-machine": "명시적 상태 기계 제어",
}

SCORE_LABEL: dict[str, str] = {
    "evidence_strength": "증거 강도",
    "patent_potential": "Patent 가능성",
    "paper_potential": "Paper 가능성",
    "novelty_confidence": "Novelty Confidence (조사 우선순위)",
}

# Mechanism families whose presence genuinely implies a designed technique
# rather than ordinary plumbing. Used by the novelty-priority heuristic.
HIGH_SPECIFICITY_MECHANISMS = {
    "prediction",
    "synchronization-mechanism",
    "adaptive-control",
    "arbitration",
}

# Mechanism families that suggest a measurable, comparable experiment, which is
# what a paper needs.
ALGORITHMIC_MECHANISMS = {
    "prediction",
    "adaptive-control",
    "arbitration",
    "scheduling-mechanism",
    "partitioning",
}

TERM_DISPLAY: dict[str, str] = {
    # adaptive control
    "adaptive": "적응 제어",
    "adaptively": "적응 제어",
    "closed loop": "closed-loop 제어",
    "closed-loop": "closed-loop 제어",
    "feedback loop": "feedback 제어",
    "feedback control": "feedback 제어",
    "hysteresis": "hysteresis 감쇠",
    "damping factor": "감쇠 계수 제어",
    "controller gain": "제어기 gain 조정",
    "pid controller": "PID 제어",
    "self tuning": "자기 조정 제어",
    "auto adjust": "자동 조정",
    "dynamic adjustment": "동적 조정",
    # prediction
    "predict": "예측",
    "estimator": "estimator 기반 보정",
    "estimation model": "추정 모델",
    "extrapolat": "외삽",
    "forecast": "예측 추정",
    "anticipat": "선행 대응 제어",
    "kalman": "Kalman 필터링",
    "model based": "모델 기반 추정",
    "look ahead": "look-ahead 계획",
    "lookahead": "look-ahead 계획",
    "trend analysis": "추세 분석",
    "projected value": "값 투영",
    # arbitration
    "arbitrat": "중재",
    "negotiat": "협상",
    "prioritis": "우선순위 결정",
    "prioritiz": "우선순위 결정",
    "quota": "quota 할당",
    "fair share": "공정 배분",
    "fairness": "공정성 정책",
    "weighted allocation": "가중 할당",
    "allocation policy": "할당 정책",
    "contention resolution": "경합 해소",
    "bidding": "입찰 기반 할당",
    "admission control": "수용 제어",
    # scheduling
    "deadline aware": "deadline 인식 스케줄링",
    "deadline-aware": "deadline 인식 스케줄링",
    "earliest deadline": "최단 deadline 우선 스케줄링",
    "pacing algorithm": "pacing 알고리즘",
    "coalesc": "요청 병합",
    "batching strategy": "일괄 처리 전략",
    "deferred execution": "지연 실행",
    "speculative execution": "투기적 실행",
    "prefetch": "선인출",
    "pipelined execution": "파이프라인 실행",
    "work stealing": "work stealing",
    "rate limit": "처리율 제한",
    # buffers
    "buffer recycling": "buffer 재활용",
    "watermark": "watermark 기반 buffer 제어",
    "high water mark": "high-watermark 제어",
    "low water mark": "low-watermark 제어",
    "ring buffer": "ring buffer 운용",
    "double buffer": "이중 buffer 운용",
    "triple buffer": "삼중 buffer 운용",
    "lazy allocation": "지연 할당",
    "slab": "slab 할당",
    "arena allocator": "arena 할당",
    "reference counted buffer": "참조 계수 buffer",
    "fence signal": "fence 신호 처리",
    "deferred release": "지연 해제",
    # synchronisation
    "phase lock": "위상 고정",
    "drift compensat": "drift 보정",
    "clock recovery": "clock 복원",
    "epoch": "epoch 추적",
    "sequence number": "시퀀스 번호 부여",
    "monotonic clock": "monotonic clock 기준 시간축",
    "barrier synchroni": "barrier 동기화",
    "handshake protocol": "handshake 프로토콜",
    "time base": "공유 기준 시간축",
    "offset correction": "offset 보정",
    "timestamp interpolat": "timestamp 보간",
    "master slave sync": "master-slave 동기화",
    # calibration
    "calibrat": "캘리브레이션",
    "compensat": "보정",
    "correction curve": "보정 곡선",
    "lookup table": "lookup table 기반 보정",
    "interpolat": "보간",
    "per unit tuning": "개체별 튜닝",
    "golden reference": "golden reference 캘리브레이션",
    "characteris": "특성화",
    "characteriz": "특성화",
    # resilience
    "graceful degrad": "점진적 성능 저하",
    "fallback strategy": "fallback 전략",
    "fall back to": "fallback 경로",
    "recovery path": "복구 경로",
    "retry policy": "재시도 정책",
    "guard band": "guard band 여유",
    "safety margin": "안전 여유",
    "watchdog": "watchdog 복구",
    "circuit breaker": "circuit breaker",
    "self heal": "자기 복구",
    # partitioning
    "offload": "연산 오프로딩",
    "partition": "분할",
    "delegat": "위임",
    "heterogeneous": "이기종 실행",
    "accelerator": "accelerator 오프로딩",
    "hybrid execution": "혼합 실행",
    "split across": "작업 분산",
    "workload distribution": "작업 부하 분산",
    "hardware assisted": "하드웨어 보조 실행",
    # state machine
    "state machine": "명시적 상태 기계",
    "transition table": "전이 표",
    "handshake": "handshake 프로토콜",
    "protocol phase": "프로토콜 단계 구분",
    "finite state": "유한 상태 제어",
    "state transition": "상태 전이 제어",
}


def display_term(term: str) -> str:
    return TERM_DISPLAY.get(term.lower().strip(), term.strip())


def domain_label(family: str) -> str:
    return DOMAIN_LABEL.get(family, family.replace("-", " "))


def problem_label(family: str) -> str:
    return PROBLEM_LABEL.get(family, family.replace("-", " "))


def mechanism_label(family: str) -> str:
    return MECHANISM_LABEL.get(family, family.replace("-", " "))


def score_label(key: str) -> str:
    return SCORE_LABEL.get(key, key)


# Variation ideas per mechanism family. These are generated prompts for a human
# reviewer, never findings, and the site labels them as such.
VARIATION_IDEAS: dict[str, list[str]] = {
    "prediction": [
        "해석적 estimator를 실제 캡처 trace로 학습한 모델로 대체합니다.",
        "동일한 예측을 다른 제어 대상에 적용합니다. 예를 들어 노출 대신 렌즈 위치에 적용합니다.",
        "예측 구간을 늘리면서 정확도와 안정성 사이의 교환 관계를 조사합니다.",
    ],
    "adaptive-control": [
        "적응 속도를 고정 상수가 아니라 측정된 노이즈의 함수로 만듭니다.",
        "동일한 closed loop를 다른 계층에 적용합니다. 예를 들어 HAL 대신 Driver에 적용합니다.",
        "Sensor mode가 바뀔 때 루프가 발진하지 않도록 명시적인 안정성 한계를 추가합니다.",
    ],
    "arbitration": [
        "정적 우선순위를 각 Stream의 남은 deadline 여유로 계산한 입찰값으로 대체합니다.",
        "중재 범위를 단일 Camera session 내부가 아니라 프로세스 사이로 확장합니다.",
        "기아 상태가 발생하지 않음을 보장하고 최악의 대기 시간을 해석적으로 증명합니다.",
    ],
    "scheduling-mechanism": [
        "deadline을 설정 상수가 아니라 Sensor의 프레임 간격에서 도출합니다.",
        "병합 범위를 단일 카메라 내부가 아니라 카메라 사이로 확장합니다.",
        "스케줄러를 전력 상태 모델과 결합하여 유휴 구간을 활용합니다.",
    ],
    "buffer-mechanism": [
        "Pool 크기를 컴파일 시점 상수가 아니라 측정된 Pipeline depth에서 결정합니다.",
        "하나의 Pool을 여러 카메라가 공유하도록 하고 카메라별 quota를 둡니다.",
        "Buffer를 투기적으로 해제하고 필요할 때 회수합니다.",
    ],
    "synchronization-mechanism": [
        "기준 시간축을 외부 trigger가 아니라 Sensor 데이터 스트림에서 복원합니다.",
        "readout 타이밍이 서로 다른 이기종 Sensor까지 동기화 범위를 확장합니다.",
        "잔여 offset을 한정하고 이를 프레임별 품질 신호로 노출합니다.",
    ],
    "calibration": [
        "보정값을 저장된 표가 아니라 캡처된 프레임에서 온라인으로 도출합니다.",
        "캘리브레이션 지점 사이를 선형이 아니라 물리 모델로 보간합니다.",
        "캘리브레이션 자체의 drift를 검출하여 재캘리브레이션을 촉발합니다.",
    ],
    "resilience": [
        "성능 저하 모드를 고정 순서가 아니라 예측된 복구 시간으로 선택합니다.",
        "성능 저하 결정을 애플리케이션에 노출하여 애플리케이션이 스스로 적응하게 합니다.",
        "모드 사이에서 발진하지 않도록 hysteresis 구간을 추가합니다.",
    ],
    "partitioning": [
        "분할 지점을 빌드 시점이 아니라 측정된 부하에 따라 런타임에 결정합니다.",
        "다른 종류의 accelerator로 분할합니다. 예를 들어 GPU 대신 NPU로 분할합니다.",
        "분할 구간을 겹쳐서 전송 비용을 연산 뒤에 숨깁니다.",
    ],
    "state-machine": [
        "전이 표를 형식 명세에서 생성하고 도달 가능성을 검증합니다.",
        "시간 기반 전이를 추가하여 외부 watchdog 없이 정지 상태를 검출합니다.",
        "상태를 trace 이벤트로 노출하여 사후 분석이 가능하게 합니다.",
    ],
}


def variation_ideas(family: str) -> list[str]:
    return VARIATION_IDEAS.get(family, [
        "해당 메커니즘을 Camera Stack의 다른 계층으로 일반화합니다.",
        "고정 정책을 런타임 측정값에서 도출한 정책으로 대체합니다.",
    ])


# Metrics that are meaningful per domain. Used when no measured number exists in
# the repository, in which case the suggestion is marked LOW_CONFIDENCE.
DOMAIN_METRICS: dict[str, list[str]] = {
    "sensor-sync": ["카메라 간 timestamp offset (us)", "시간에 따른 offset 표준편차",
                    "수렴까지 소요된 프레임 수"],
    "scheduling": ["end-to-end 프레임 지연 (ms)", "deadline 위반율 (%)", "큐 점유율 분포"],
    "memory-buffer": ["최대 buffer 개수", "시간당 할당 실패 횟수", "buffer 재사용률"],
    "performance": ["초당 프레임 수 (fps)", "프레임 시간 p99 (ms)", "CPU 부하 (%)"],
    "3a": ["수렴까지 소요된 프레임 수", "정상 상태 노출 오차", "발진 진폭"],
    "isp": ["기준 영상 대비 PSNR", "SSIM", "프레임당 처리 시간 (ms)"],
    "image-quality": ["PSNR", "SSIM", "주관 평가 선호도 점수"],
    "multi-camera": ["카메라 간 timestamp 산포 (us)", "카메라별 프레임 손실 수"],
    "computational-photography": ["기준 영상 대비 PSNR", "캡처부터 결과까지 지연 (ms)"],
    "ai-camera": ["추론 지연 (ms)", "Ground truth 대비 정확도", "추론당 전력 (mW)"],
    "camera-pipeline": ["Pipeline depth", "Request 완료 지연 (ms)"],
    "sensor-control": ["노출 적용 지연 (프레임)", "gain 정확도 오차"],
    "camera-hal": ["Request 왕복 지연 (ms)", "Request당 HAL 오버헤드 (us)"],
    "camera-framework": ["구성 소요 시간 (ms)", "Request 처리량"],
    "camera-driver": ["ioctl 지연 (us)", "시간당 buffer 손실 수"],
    "debug-profiling": ["tracing 오버헤드 (%)", "초당 수집 이벤트 수"],
}


def domain_metrics(family: str) -> list[str]:
    return DOMAIN_METRICS.get(family, ["지연 (ms)", "처리량", "실패율"])


# Evidence kind labels used by both the pipeline prose and the site.
EVIDENCE_KIND_LABEL: dict[str, str] = {
    "code": "Source Code",
    "doc": "문서",
    "commit": "Commit",
    "pull_request": "Pull Request",
    "issue": "Issue",
    "release": "Release",
    "config": "설정 및 빌드 파일",
}


def evidence_kind_label(kind: str) -> str:
    return EVIDENCE_KIND_LABEL.get(kind, kind)
