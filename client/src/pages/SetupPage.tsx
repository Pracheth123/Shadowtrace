import { useEffect, useRef, useState } from "react";
import { CircleAlertIcon, LoaderCircleIcon } from "lucide-react";

import { ResumeUpload } from "@/components/ResumeUpload";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardDescription,
  CardFooter,
  CardHeader,
  CardPanel,
  CardTitle,
} from "@/components/ui/card";
import { Field, FieldDescription, FieldLabel, Form } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Select } from "@/components/ui/select";
import {
  RadioGroupPrimitive,
  RadioPrimitive,
} from "@/components/ui/radio-group";
import {
  ApiError,
  ROLE_FAMILIES,
  ROUNDS,
  SENIORITIES,
  api,
  ensureGuest,
  HTTP_BASE,
  type IntakeResult,
  type Intensity,
  type Lane,
  type RoleFamily,
  type RoundChoice,
  type Seniority,
} from "@/lib/api";
import {
  segmentedControlItemVariants,
  segmentedControlRootClassName,
} from "@/lib/segmented-control";
import type { SessionOptions } from "@/lib/use-session";

const itemClassName = segmentedControlItemVariants({
  className: "grow",
  state: "checked",
});

// Difficulty is how hard the follow-ups press. It is not the round selection:
// "Panel" difficulty is the deepest follow-up policy, not a panel of voices.
const INTENSITIES: { label: string; value: Intensity; hint: string }[] = [
  { label: "Coach", value: "coach", hint: "Hints offered, one level of follow-up." },
  { label: "Realistic", value: "realistic", hint: "No hints. Follow-ups go two levels deep." },
  { label: "Hard", value: "panel", hint: "No hints. Follow-ups go up to three levels deep." },
];

const STAGE_LABEL: Record<string, string> = {
  queued: "Queued",
  reading_documents: "Reading your documents",
  indexing_repository: "Reading the repository (read-only)",
  building_claims: "Finding statements to ask about",
  preparing_guidance: "Preparing your guidance",
  ready: "Ready",
  failed: "Failed",
};

const EVIDENCE_LABEL: Record<string, string> = {
  candidate_assertion: "your statement",
  repository: "in the repository",
  work_sample: "in your work sample",
};

export type SetupPageProps = {
  onStart: (options: SessionOptions) => void;
  onOpenHistory: () => void;
};

type Phase =
  | { kind: "form" }
  | { kind: "processing"; intakeId: string; result?: IntakeResult }
  | { kind: "review"; intakeId: string; result: IntakeResult };

export function SetupPage({ onStart, onOpenHistory }: SetupPageProps) {
  const [phase, setPhase] = useState<Phase>({ kind: "form" });
  const [error, setError] = useState<{ message: string; recovery?: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [resume, setResume] = useState<File | null>(null);
  const [background, setBackground] = useState("");
  const [targetRole, setTargetRole] = useState("");
  const [family, setFamily] = useState<RoleFamily>("software");
  const [seniority, setSeniority] = useState<Seniority>("mid");
  const [round, setRound] = useState<RoundChoice>("full");
  const [lane, setLane] = useState<Lane>("voice");
  const [intensity, setIntensity] = useState<Intensity>("realistic");
  const [jobDescription, setJobDescription] = useState("");
  const [company, setCompany] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [workSample, setWorkSample] = useState<File | null>(null);
  const [health, setHealth] = useState<Record<string, any> | null>(null);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    void ensureGuest().catch(() => undefined);
    fetch(`${HTTP_BASE}/health`)
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setHealth(null));
    return () => {
      if (pollRef.current) window.clearTimeout(pollRef.current);
    };
  }, []);

  const voiceAvailable = health?.providers?.voice === "deepgram";

  const poll = (intakeId: string) => {
    api<IntakeResult>(`/api/intake/${intakeId}`)
      .then((result) => {
        const state = result.status.state;
        if (state === "ready") {
          setPhase({ kind: "review", intakeId, result });
          return;
        }
        if (state === "failed") {
          setPhase({ kind: "form" });
          setError({
            message: result.status.error ?? "Preparing your interview failed.",
            recovery: result.status.recovery,
          });
          return;
        }
        setPhase({ kind: "processing", intakeId, result });
        pollRef.current = window.setTimeout(() => poll(intakeId), 700);
      })
      .catch((err: ApiError) => {
        setPhase({ kind: "form" });
        setError({ message: err.message, recovery: err.recovery });
      });
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!targetRole.trim()) {
      setError({ message: "Add the role you are preparing for." });
      return;
    }
    if (!resume && background.trim().length < 40) {
      setError({
        message: "Add your background: upload a resume, or write a few sentences about your experience.",
      });
      return;
    }
    const form = new FormData();
    form.set("target_role", targetRole.trim());
    form.set("role_family", family);
    form.set("seniority", seniority);
    form.set("round", round);
    form.set("lane", lane);
    form.set("intensity", intensity);
    form.set("background_text", background);
    form.set("job_description", jobDescription);
    form.set("company_context", company);
    form.set("repo_url", repoUrl.trim());
    if (resume) form.set("resume", resume);
    if (workSample) form.set("work_sample", workSample);
    setSubmitting(true);
    try {
      const created = await api<{ intake_id: string }>("/api/intake", {
        method: "POST",
        body: form,
      });
      setPhase({ kind: "processing", intakeId: created.intake_id });
      poll(created.intake_id);
    } catch (err) {
      const apiError = err as ApiError;
      setError({ message: apiError.message, recovery: apiError.recovery });
    } finally {
      setSubmitting(false);
    }
  };

  if (phase.kind === "processing") {
    const status = phase.result?.status;
    const index = status?.stage_index ?? 0;
    const count = status?.stage_count ?? 5;
    return (
      <div className="mx-auto flex w-full max-w-xl flex-col gap-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <LoaderCircleIcon className="size-4 animate-spin" />
              Preparing your interview
            </CardTitle>
            <CardDescription>
              {STAGE_LABEL[status?.state ?? "queued"] ?? status?.state}
            </CardDescription>
          </CardHeader>
          <CardPanel>
            <Progress value={index} max={count} label={`Step ${index} of ${count}`} />
            <p className="mt-2 text-xs text-muted-foreground">
              Your files are read as data. A repository is read, never run, and is
              deleted after its README and manifests are excerpted.
            </p>
          </CardPanel>
        </Card>
      </div>
    );
  }

  if (phase.kind === "review") {
    const { result, intakeId } = phase;
    const claims = result.claims ?? [];
    const prep = result.prep?.items ?? [];
    const fit = result.fit_gap;
    const warnings = result.status.warnings ?? [];
    const cfg = result.config ?? {};
    const chosenLane = String(cfg.lane ?? lane) as Lane;
    return (
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-5">
        <header className="flex flex-col gap-1">
          <h1>Your preparation</h1>
          <p className="text-sm text-muted-foreground">
            {String(cfg.target_role)} · {ROUNDS.find((r) => r.value === cfg.round)?.label} ·{" "}
            {chosenLane === "text" ? "typed" : "spoken"} ·{" "}
            {INTENSITIES.find((i) => i.value === cfg.intensity)?.label} difficulty
          </p>
          <p className="text-xs text-muted-foreground">{result.coverage_note}</p>
        </header>

        {warnings.map((warning) => (
          <p key={warning} className="rounded-md bg-muted px-3 py-2 text-sm">{warning}</p>
        ))}

        <Card>
          <CardHeader>
            <CardTitle className="text-base">What the interviewers will ask about</CardTitle>
            <CardDescription>{result.evidence_note}</CardDescription>
          </CardHeader>
          <CardPanel>
            {claims.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No specific statements were found. Follow-ups will anchor on what you say.
              </p>
            ) : (
              <ul className="flex flex-col gap-2">
                {claims.map((claim) => (
                  <li key={claim.id} className="text-sm">
                    “{claim.text}”{" "}
                    <Badge variant="muted" size="sm">
                      {EVIDENCE_LABEL[claim.evidence_kind] ?? claim.evidence_kind}
                      {claim.evidence_kind === "repository" ? ` · ${claim.source_path}` : ""}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </CardPanel>
        </Card>

        {fit && fit.required.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Job description overlap</CardTitle>
              <CardDescription>
                Terms from the job description compared with your background. For
                preparation only — not a score.
              </CardDescription>
            </CardHeader>
            <CardPanel className="flex flex-col gap-1 text-sm">
              {fit.matched.length > 0 && <p>Mentioned in your background: {fit.matched.join(", ")}</p>}
              {fit.missing.length > 0 && <p>Not mentioned: {fit.missing.join(", ")}</p>}
            </CardPanel>
          </Card>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="text-base">How to prepare</CardTitle>
          </CardHeader>
          <CardPanel>
            <ol className="flex flex-col gap-3">
              {prep.map((item, index) => (
                <li key={item.id} className="flex gap-3">
                  <Badge variant="primary" className="mt-0.5 shrink-0">{index + 1}</Badge>
                  <div className="text-sm">
                    <p className="font-medium">{item.title}</p>
                    <p className="text-muted-foreground">{item.detail}</p>
                    <p>{item.action}</p>
                    {item.evidence.map((ev, i) => (
                      <p key={i} className="text-xs text-muted-foreground">
                        From {ev.source}: “{ev.excerpt}”
                      </p>
                    ))}
                  </div>
                </li>
              ))}
            </ol>
          </CardPanel>
        </Card>

        <div className="flex flex-wrap gap-2">
          <Button
            size="lg"
            onClick={() =>
              onStart({
                intakeId,
                lane: chosenLane,
                intensity: String(cfg.intensity ?? intensity) as Intensity,
              })
            }
          >
            Start the interview
          </Button>
          <Button variant="ghost" onClick={() => setPhase({ kind: "form" })}>
            Change setup
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-6">
      <header className="flex flex-col gap-1">
        <div className="flex items-center justify-between">
          <h1>Shadow Trace</h1>
          <Button variant="ghost" size="sm" onClick={onOpenHistory}>
            Your history
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">
          Interview practice built from your own background. Questions follow up
          on what you have actually done; feedback quotes what you actually said.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Set up your interview</CardTitle>
          <CardDescription>Everything here changes the interview you get.</CardDescription>
        </CardHeader>

        <CardPanel>
          <Form className="w-full" onSubmit={submit}>
            <Field>
              <FieldLabel>Your resume</FieldLabel>
              <ResumeUpload onFileChange={setResume} />
            </Field>

            <Field>
              <FieldLabel>Or describe your background</FieldLabel>
              <textarea
                className="min-h-24 w-full rounded-md border border-input bg-card px-3 py-2 text-sm"
                placeholder="A few sentences about what you have done: 'I led…', 'I built…', 'I closed…'"
                value={background}
                onChange={(event) => setBackground(event.target.value)}
                maxLength={20000}
              />
              <FieldDescription>
                Used with or instead of a resume. Follow-ups are built from these statements.
              </FieldDescription>
            </Field>

            <Field>
              <FieldLabel>Target role</FieldLabel>
              <Input
                placeholder="e.g. Backend engineer, Account executive"
                value={targetRole}
                onChange={(event) => setTargetRole(event.target.value)}
                maxLength={120}
              />
            </Field>

            <Field>
              <FieldLabel>Profession</FieldLabel>
              <Select
                items={ROLE_FAMILIES}
                value={family}
                onChange={(event) => setFamily(event.target.value as RoleFamily)}
              />
              <FieldDescription>
                Picks the domain specialist's questions and rubric.
              </FieldDescription>
            </Field>

            <Field>
              <FieldLabel>Seniority</FieldLabel>
              <Select
                items={SENIORITIES}
                value={seniority}
                onChange={(event) => setSeniority(event.target.value as Seniority)}
              />
              <FieldDescription>Senior and lead roles add leadership follow-ups.</FieldDescription>
            </Field>

            <Field>
              <FieldLabel>Interview round</FieldLabel>
              <Select
                items={ROUNDS.map(({ label, value }) => ({ label, value }))}
                value={round}
                onChange={(event) => setRound(event.target.value as RoundChoice)}
              />
              <FieldDescription>{ROUNDS.find((r) => r.value === round)?.hint}</FieldDescription>
            </Field>

            <Field>
              <FieldLabel id="lane-label">How you will answer</FieldLabel>
              <RadioGroupPrimitive
                aria-labelledby="lane-label"
                className={segmentedControlRootClassName}
                value={lane}
                onValueChange={(next) => setLane(next as Lane)}
                name="lane"
              >
                <RadioPrimitive.Root className={itemClassName} value="voice">
                  Speak
                </RadioPrimitive.Root>
                <RadioPrimitive.Root className={itemClassName} value="text">
                  Type
                </RadioPrimitive.Root>
              </RadioGroupPrimitive>
              <FieldDescription>
                {lane === "text"
                  ? "Typing is fine. Spoken delivery is not assessed or scored."
                  : voiceAvailable
                    ? "You will need microphone access. You can switch to typing if voice fails."
                    : "The server reports no speech provider; this would run with mock audio in development, or be refused in production. Typing is recommended."}
              </FieldDescription>
            </Field>

            <Field>
              <FieldLabel id="intensity-label">Difficulty</FieldLabel>
              <RadioGroupPrimitive
                aria-labelledby="intensity-label"
                className={segmentedControlRootClassName}
                value={intensity}
                onValueChange={(next) => setIntensity(next as Intensity)}
                name="intensity"
              >
                {INTENSITIES.map((item) => (
                  <RadioPrimitive.Root key={item.value} className={itemClassName} value={item.value}>
                    {item.label}
                  </RadioPrimitive.Root>
                ))}
              </RadioGroupPrimitive>
              <FieldDescription>
                {INTENSITIES.find((item) => item.value === intensity)?.hint} One interviewer
                speaks at a time at every difficulty.
              </FieldDescription>
            </Field>

            <Field>
              <FieldLabel>Job description (optional)</FieldLabel>
              <textarea
                className="min-h-20 w-full rounded-md border border-input bg-card px-3 py-2 text-sm"
                placeholder="Paste the job description, ideally including its requirements list."
                value={jobDescription}
                onChange={(event) => setJobDescription(event.target.value)}
                maxLength={20000}
              />
              <FieldDescription>Used for preparation guidance on terms your background does not mention.</FieldDescription>
            </Field>

            <Field>
              <FieldLabel>Company context (optional)</FieldLabel>
              <textarea
                className="min-h-16 w-full rounded-md border border-input bg-card px-3 py-2 text-sm"
                placeholder="What you know about the company. No research is done for you."
                value={company}
                onChange={(event) => setCompany(event.target.value)}
                maxLength={4000}
              />
            </Field>

            <Field>
              <FieldLabel>Repository (optional)</FieldLabel>
              <Input
                placeholder="https://github.com/you/project"
                value={repoUrl}
                onChange={(event) => setRepoUrl(event.target.value)}
              />
              <FieldDescription>
                Public https repository on GitHub, GitLab, Bitbucket or Codeberg. Read,
                never run. It shows the code exists — not who wrote it. Not required for any role.
              </FieldDescription>
            </Field>

            <Field>
              <FieldLabel>Work sample (optional)</FieldLabel>
              <ResumeUpload onFileChange={setWorkSample} noun="work sample" />
              <FieldDescription>A document you wrote, e.g. a plan, analysis or design doc.</FieldDescription>
            </Field>

            {error && (
              <div role="alert" className="flex gap-2 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                <CircleAlertIcon className="mt-0.5 size-4 shrink-0" />
                <div>
                  <p>{error.message}</p>
                  {error.recovery && <p className="text-xs">{error.recovery}</p>}
                </div>
              </div>
            )}

            <Button className="w-full" size="lg" type="submit" disabled={submitting}>
              {submitting ? "Uploading…" : "Prepare my interview"}
            </Button>
          </Form>
        </CardPanel>

        <CardFooter>
          <div className="flex gap-1.5 text-xs text-muted-foreground">
            <CircleAlertIcon className="mt-px size-3 shrink-0" />
            <p>
              Practice only — nothing here screens or ranks you. You are a guest in
              this browser: clearing site data loses your history.
            </p>
          </div>
        </CardFooter>
      </Card>
    </div>
  );
}
