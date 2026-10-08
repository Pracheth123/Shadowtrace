// "Papers to Practice" scroll story: data only. One person's background (never
// competing candidates), prewritten example interview content, and stable paper
// trajectories. Nothing here is generated, uploaded or sent anywhere.

export type PaperKind = "resume" | "projects" | "skills" | "experience" | "sample";
export type StoryMode = "pinned" | "compact" | "static";
export type StageIndex = 0 | 1 | 2 | 3;

/** [x, y, rotate°, scale, opacity]. x/y are fractions of the stage width/height. */
export type Pose = readonly [number, number, number, number, number];
/** One pose per stage: scattered, gathered, interview, feedback. */
export type Track = readonly [Pose, Pose, Pose, Pose];

/**
 * Local section progress (0–1) → stage. Each stage holds still for most of its
 * range so captions and examples have time to be read; the moves happen in the
 * short windows between holds. Starting values from the brief, tuned visually.
 *   A scattered  0.00–0.22   hold 0.00–0.08
 *   B gathered   0.22–0.48   hold 0.26–0.44
 *   C interview  0.48–0.75   hold 0.56–0.72
 *   D feedback   0.75–1.00   hold 0.84–1.00
 */
export const HOLDS: readonly (readonly [number, number])[] = [[0, 0.08], [0.26, 0.44], [0.56, 0.72], [0.84, 1]];
/** Where a jump to each stage lands: inside its hold. */
export const STAGE_TARGET = [0.02, 0.35, 0.64, 0.93] as const;
export function stageOf(progress: number): StageIndex {
  return progress < 0.17 ? 0 : progress < 0.5 ? 1 : progress < 0.78 ? 2 : 3;
}

export const STAGES = [
  { short: "Papers", title: "You’ve done the work.", text: "Projects, roles and skills, spread across your papers." },
  { short: "Background", title: "Start with your experience.", text: "You review the statements the interviewers will use. Example highlights shown." },
  { short: "Interview", title: "Experience becomes a question.", text: "Each round asks about the same background in its own way." },
  { short: "Coaching", title: "Find your next clearer answer.", text: "Feedback quotes the answer and suggests one thing to practise." },
] as const;

export const BACKGROUND_STATEMENT = "I helped launch a new service.";
export const SECOND_STATEMENT = "Worked with support on onboarding.";

export interface StoryRole {
  id: string;
  role: string;
  question: string;
  answer: string;
  /** Exact substring of `answer`: feedback quotes the candidate answer, not the resume. */
  quote: string;
  coaching: string;
}

export const STORY_ROLES: StoryRole[] = [
  {
    id: "hr", role: "HR",
    question: "What was your part in the launch, and why did it matter to you?",
    answer: "I wrote the rollout checklist because new customers kept asking the same questions.",
    quote: "new customers kept asking the same questions",
    coaching: "Say which questions stopped coming up, and how you noticed.",
  },
  {
    id: "hm", role: "Hiring manager",
    question: "What did you personally own, and how did you check the result?",
    answer: "I designed the rollout checklist and checked support requests after launch.",
    quote: "checked support requests after launch",
    coaching: "Explain what changed in those requests, and what you did next.",
  },
  {
    id: "ds", role: "Specialist",
    question: "How did you plan the rollout, and what risk were you managing?",
    answer: "I released it in stages and reviewed support requests after each one.",
    quote: "released it in stages",
    coaching: "Explain why a staged release fit this risk, and what you would watch next time.",
  },
];

// Desktop (pinned) trajectories. Papers are centred; cards are anchored by their
// top-left corner. Supporting sheets settle behind the resume, then leave.
export const PINNED_PAPERS: Record<PaperKind, Track> = {
  resume:     [[0.02, 0.02, -5, 0.92, 1],    [0, -0.02, 0, 1.1, 1],      [-0.27, -0.17, -2, 0.78, 1], [-0.33, -0.29, -3, 0.56, 1]],
  projects:   [[-0.29, -0.27, -13, 0.9, 1],  [-0.04, -0.05, -4, 1.12, 1], [-0.31, -0.21, -6, 0.86, 1], [-0.78, -0.58, -20, 0.8, 0]],
  skills:     [[0.31, -0.3, 11, 0.9, 1],     [0.045, -0.035, 3, 1.12, 1], [-0.23, -0.2, 4, 0.84, 1],   [-0.62, 0.66, 18, 0.8, 0]],
  experience: [[-0.31, 0.29, 9, 0.92, 1],    [-0.035, 0.035, -2, 1.12, 1], [-0.3, -0.12, -3, 0.84, 1],  [-0.88, 0.12, -14, 0.8, 0]],
  sample:     [[0.3, 0.3, -10, 0.92, 1],     [0.05, 0.045, 4, 1.12, 1],  [-0.22, -0.13, 5, 0.84, 1],  [-0.5, 0.82, 22, 0.8, 0]],
};
export const PINNED_CARDS: Record<"interview" | "feedback", Track> = {
  interview: [[-0.07, 0.24, 2, 0.94, 0], [-0.07, 0.16, 1, 0.96, 0], [-0.07, -0.1, 0, 1, 1], [-0.07, -0.45, 0, 0.94, 1]],
  feedback:  [[-0.36, 0.34, -2, 0.94, 0], [-0.36, 0.3, -2, 0.94, 0], [-0.36, 0.24, -1, 0.95, 0], [-0.38, 0.05, 0, 1, 1]],
};

// Compact (mobile / short screens): three papers, smaller travel, cards full width.
export const COMPACT_PAPERS: Partial<Record<PaperKind, Track>> = {
  resume:     [[0.05, 0.02, -5, 0.9, 1],    [0, 0, 0, 1.06, 1],       [-0.29, -0.27, -3, 0.5, 1],  [-0.31, -0.28, -3, 0.44, 1]],
  projects:   [[-0.24, -0.24, -11, 0.86, 1], [-0.04, -0.04, -4, 1.04, 1], [-0.32, -0.3, -7, 0.46, 1],  [-0.85, -0.6, -18, 0.5, 0]],
  experience: [[0.25, 0.26, 9, 0.86, 1],    [0.04, 0.04, 3, 1.04, 1],   [-0.26, -0.24, 5, 0.46, 1],  [0.85, -0.5, 16, 0.5, 0]],
};
export const COMPACT_CARDS: Record<"interview" | "feedback", Track> = {
  interview: [[-0.5, 0.12, 0, 0.96, 0], [-0.5, 0.06, 0, 0.96, 0], [-0.5, -0.19, 0, 1, 1], [-0.5, -0.26, 0, 0.96, 0]],
  feedback:  [[-0.5, 0.12, 0, 0.96, 0], [-0.5, 0.1, 0, 0.96, 0], [-0.5, 0.04, 0, 0.96, 0], [-0.5, -0.12, 0, 1, 1]],
};

/** Paper width in cqmin of the stage, per layout. */
export const PAPER_WIDTH: Record<"pinned" | "compact", Partial<Record<PaperKind, number>>> = {
  pinned: { resume: 44, projects: 34, skills: 30, experience: 34, sample: 32 },
  compact: { resume: 58, projects: 44, experience: 44 },
};
export const PAPER_RATIO = 1.32; // height / width
/** Where background statement 1 sits on the resume, as fractions of its size from the centre. */
export const STATEMENT_ANCHOR = { x: 0.47, y: 0.04 } as const;
