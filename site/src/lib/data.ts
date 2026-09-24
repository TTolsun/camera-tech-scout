/**
 * Build-time data access.
 *
 * The pipeline writes flat JSON into `data/`. The site reads it with `fs` rather
 * than with a JSON import, so that the directory can be pointed elsewhere (a dry
 * run writes to `data-dryrun/`) without touching any import path.
 *
 * Every accessor degrades to an empty shape when a file is missing, so the site
 * still builds before the first scan has run.
 */

import fs from 'node:fs';
import path from 'node:path';

// Resolved against the working directory, not against `import.meta.url`: this
// module is bundled during the build, so its own URL no longer points at
// `src/lib` and a path relative to it would silently miss the data directory.
// Astro always runs with the site directory as the working directory.
export const DATA_DIR = path.resolve(
  process.cwd(),
  process.env.SCOUT_DATA_DIR || path.join('..', 'data'),
);

function read<T>(file: string, fallback: T): T {
  const target = path.join(DATA_DIR, file);
  try {
    return JSON.parse(fs.readFileSync(target, 'utf8')) as T;
  } catch {
    return fallback;
  }
}

export const dataAvailable = fs.existsSync(path.join(DATA_DIR, 'meta.json'));

// --- shapes -----------------------------------------------------------------

export type Narrative = {
  text: string;
  evidence_ids: string[];
  confidence: string;
};

export type Score = {
  value: number;
  label: string;
  caveat: string | null;
  reasons: { text: string; delta: number; evidence_ids: string[] }[];
};

export type EvidenceRow = {
  id: string;
  repository: string;
  org: string;
  repo: string;
  kind: string;
  kindLabel: string;
  path: string | null;
  symbol: string | null;
  symbol_kind: string | null;
  line_start: number | null;
  line_end: number | null;
  commit_sha: string | null;
  ref: string | null;
  title: string;
  snippet: string;
  url: string;
  observed_at: string | null;
  first_seen_commit: string | null;
  first_seen_at: string | null;
  location: string;
  signalSummary: { kind: string; label: string; terms: string[] }[];
};

export type CriticFinding = {
  rule: string;
  title: string;
  passed: boolean;
  detail: string;
  evidence_ids: string[];
};

export type Candidate = {
  id: string;
  slug: string;
  title: string;
  type: string;
  typeLabel: string;
  status: string;
  statusLabel: string;
  statusReason: string;
  verdict: string;
  verdictLabel: string;
  topic: string;
  topicLabel: string;
  mechanismFamily: string;
  mechanismLabel: string;
  summary: string;
  problem: Narrative;
  existingApproach: Narrative;
  proposedTechnique: Narrative;
  difference: Narrative;
  technicalEffect: Narrative;
  patentAngle: Record<string, any>;
  paperAngle: Record<string, any>;
  priorArtKeywords: string[];
  evidenceIds: string[];
  evidence: EvidenceRow[];
  evidenceByKind: { kind: string; label: string; count: number }[];
  repositories: string[];
  relatedCandidates: { id: string; title: string }[];
  technologies: string[];
  technologyLabels: string[];
  scores: Record<string, Score>;
  criticFindings: CriticFinding[];
  counterEvidence: CriticFinding[];
  timeline: { at: string; kind: string; kindLabel: string; title: string; url: string }[];
  firstSeenAt: string | null;
  lastUpdatedAt: string | null;
};

export type CandidateCard = Omit<
  Candidate,
  | 'problem' | 'existingApproach' | 'proposedTechnique' | 'difference'
  | 'technicalEffect' | 'patentAngle' | 'paperAngle' | 'priorArtKeywords'
  | 'evidence' | 'evidenceByKind' | 'criticFindings' | 'counterEvidence'
  | 'timeline' | 'relatedCandidates' | 'technologyLabels' | 'technologies'
  | 'evidenceIds' | 'statusReason'
> & { evidenceCount: number };

// --- accessors --------------------------------------------------------------

export const meta = read('meta.json', {
  runId: '',
  mode: 'unknown',
  generatedAt: null as string | null,
  startedAt: null as string | null,
  dryRun: false,
  engine: {} as Record<string, any>,
  labels: {} as Record<string, Record<string, string>>,
  disclaimer: '',
});

export const overview = read('overview.json', {
  generatedAt: null as string | null,
  dryRun: false,
  organizationCount: 0,
  organizations: [] as string[],
  repositoryCount: 0,
  lastScanTime: null as string | null,
  evidenceCount: 0,
  candidateCount: 0,
  counts: {} as Record<string, number>,
  evidenceByKind: [] as { kind: string; label: string; count: number }[],
  topCandidates: [] as CandidateCard[],
  recentTechnologyChanges: [] as any[],
  scanHealth: {} as Record<string, number>,
});

export const sources = read('sources.json', {
  organizations: [] as any[],
  repositories: [] as any[],
  skipped: [] as { target: string; reason: string }[],
  apiProblems: [] as any[],
});

export const repositories = read('repositories.json', { repositories: [] as any[] });

export const candidatesFile = read('candidates.json', {
  candidates: [] as Candidate[],
  cards: [] as CandidateCard[],
  droppedClusters: [] as any[],
});

export const technologyMap = read('technology-map.json', {
  technologies: [] as any[],
  graph: { nodes: [] as any[], edges: [] as any[], counts: {} as Record<string, number> },
  crossRepositoryTechnologies: [] as any[],
});

export const recent = read('recent.json', {
  changedCandidates: [] as CandidateCard[],
  recentEvidence: [] as EvidenceRow[],
  technologyChanges: [] as any[],
});

export const rejected = read('rejected.json', {
  rejected: [] as any[],
  note: '',
  droppedClusters: [] as any[],
  droppedNote: '',
});

export const evidence = read('evidence.json', {
  evidence: [] as EvidenceRow[],
  counts: { total: 0, byKind: {} as Record<string, number>, byRepository: {} as Record<string, number> },
});

export const history = read('history.json', {
  runs: [] as any[],
  currentRun: {} as Record<string, any>,
  repositoryStates: [] as any[],
});

export const searchIndex = read('search-index.json', [] as any[]);

export const candidates = candidatesFile.candidates;
export const candidateCards = candidatesFile.cards;

export function candidateBySlug(slug: string): Candidate | undefined {
  return candidates.find((c) => c.slug === slug);
}

// --- helpers ----------------------------------------------------------------

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '기록 없음';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    `${parsed.getUTCFullYear()}-${pad(parsed.getUTCMonth() + 1)}-${pad(parsed.getUTCDate())}` +
    ` ${pad(parsed.getUTCHours())}:${pad(parsed.getUTCMinutes())} UTC`
  );
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '기록 없음';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${parsed.getUTCFullYear()}-${pad(parsed.getUTCMonth() + 1)}-${pad(parsed.getUTCDate())}`;
}

/** The four score keys, in the order every page displays them. */
export const SCORE_KEYS = [
  'patent_potential',
  'paper_potential',
  'evidence_strength',
  'novelty_confidence',
] as const;

export const CONFIDENCE_NOTE: Record<string, string> = {
  UNKNOWN: '이 항목을 뒷받침하는 서술을 찾지 못했습니다.',
  LOW_CONFIDENCE: '서술은 있으나 이를 뒷받침하는 측정값이 없습니다.',
  NEEDS_VERIFICATION: '확인이 필요한 항목입니다. 수집된 자료만으로는 판단할 수 없습니다.',
};
