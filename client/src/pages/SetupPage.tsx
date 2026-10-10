import { useEffect, useRef, useState } from "react";
import { ArrowRightIcon, ChevronDownIcon, CircleAlertIcon, FilePlusIcon, LoaderCircleIcon, ShieldCheckIcon } from "lucide-react";

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
  jsonBody,
  type IntakeClaim,
  type IntakeResult,
  type ReviewAction,
  type Intensity,
  type Lane,
  type RoleFamily,
  type RoundChoice,
  type Seniority,
} from "@/lib/api";
import { isCaptureSupported } from "@/lib/audio-capture";
import {
  canSpeak,
  readMicPermission,
  voiceDeveloperNote,
  voiceMessage,
  voiceState,
  type MicPermission,
} from "@/lib/voice-availability";
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
  repository: "in the repository (shows content exists, not who wrote it)",
  work_sample: "in your work sample",
};

type Decision = { action: ReviewAction; text: string };

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
  const [lane, setLane] = useState<Lane>("text");
  const [intensity, setIntensity] = useState<Intensity>("realistic");
  const [jobDescription, setJobDescription] = useState("");
  const [company, setCompany] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [workSample, setWorkSample] = useState<File | null>(null);
  const [health, setHealth] = useState<Record<string, any> | null>(null);
  const [healthFailed, setHealthFailed] = useState(false);
  const [consent, setConsent] = useState(false);
  const pollRef = useRef<number | null>(null);

  const [micPermission, setMicPermission] = useState<MicPermission>("unknown");

  const loadHealth = () => {
    setHealth(null);
    setHealthFailed(false);
    fetch(`${HTTP_BASE}/health`)
      .then((r) => { if (!r.ok) throw new Error("Service unavailable"); return r.json(); })
      .then(setHealth)
      .catch(() => { setHealth(null); setHealthFailed(true); });
  };

  useEffect(() => {
    void ensureGuest().catch(() => undefined);
    loadHealth();
    // Read (never request) the microphone permission, and follow changes made
    // in the browser's site settings while this page is open.
    let status: PermissionStatus | null = null;
    const update = () => status && setMicPermission(status.state as MicPermission);
    void readMicPermission().then((result) => {
      setMicPermission(result.state);
      status = result.status;
      status?.addEventListener?.("change", update);
    });
    return () => {
      if (pollRef.current) window.clearTimeout(pollRef.current);
      status?.removeEventListener?.("change", update);
    };
  }, []);

  const voice = voiceState(health, healthFailed, {
    captureSupported: isCaptureSupported(),
    secureContext: window.isSecureContext,
    micPermission,
  });
  const voiceAvailable = canSpeak(voice);
  const developerNote = import.meta.env.DEV ? voiceDeveloperNote(voice) : null;

  // Speak is never left selected once it cannot work; the candidate sees why.
  useEffect(() => {
    if (!voiceAvailable && lane === "voice") setLane("text");
  }, [voiceAvailable, lane]);

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
    if (!consent) {
      setError({ message: "Please read and confirm how your documents and speech are processed." });
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
    form.set("consent", "1");
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
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
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
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-5">
        <JourneySteps current={1} />
        <header className="workspace-title">
          <p className="eyebrow">CHECK THE CONTEXT</p>
          <h1>Your experience, in focus.</h1>
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

        <StatementReview
          intakeId={intakeId}
          claims={claims}
          result={result}
          onSaved={(next) => setPhase({ kind: "review", intakeId, result: next })}
          onStart={() =>
            onStart({
              intakeId,
              lane: chosenLane,
              intensity: String(cfg.intensity ?? intensity) as Intensity,
              consent: true,
            })
          }
          onChangeSetup={() => setPhase({ kind: "form" })}
        />

        {fit && fit.required.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Document overlap with the job description</CardTitle>
              <CardDescription>
                Which job-description terms also appear in the documents you supplied. This is
                document overlap only — it is not a measure of ability, and a term missing from
                your documents does not mean you lack the skill.
              </CardDescription>
            </CardHeader>
            <CardPanel className="flex flex-col gap-1 text-sm">
              {fit.matched.length > 0 && <p>Also in your documents: {fit.matched.join(", ")}</p>}
              {fit.missing.length > 0 && <p>Not in your documents: {fit.missing.join(", ")}</p>}
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

      </div>
    );
  }

  return (
    <div className="w-full">
      <JourneySteps current={0} />
      <header className="workspace-title">
        <p className="eyebrow">MAKE IT YOUR INTERVIEW</p>
        <h1>A little context goes a long way.</h1>
        <p>Bring your experience. Choose the conversation you want to practise.</p>
      </header>
      <div className="provider-status" role="status">
        <ShieldCheckIcon aria-hidden="true" />
        <p>{health === null ? healthFailed ? "The interview service can’t be reached right now. Check again in a moment, or reload this page." : "Connecting to the interview service…" : health.providers?.interviewer === "groq" ? <><strong>Interview service configured.</strong> {voiceAvailable ? "Voice and typing are configured. Provider access is verified when used." : "Typing is available; voice is currently unavailable."}</> : <><strong>Development mode.</strong> Questions follow a fixed plan. Feedback is a placeholder, not an AI assessment. {voiceAvailable ? "Speech is configured." : "Use typing for this preview."}</>}</p>
        {healthFailed && <Button type="button" variant="outline" size="sm" onClick={loadHealth}>Check again</Button>}
      </div>
      {developerNote && <p className="dev-note" data-testid="voice-dev-note"><strong>Developer:</strong> {developerNote}</p>}
      <div className="setup-layout">
      <Card>
        <CardPanel className="p-0">
          <Form className="setup-form w-full" onSubmit={submit}>
            <section className="setup-section" aria-labelledby="background-heading">
              <SectionHeading number="01" id="background-heading" title="Your experience" description="A resume, a few sentences, or both." />
            <Field>
              <FieldLabel>Your resume</FieldLabel>
              <ResumeUpload onFileChange={setResume} />
            </Field>

            <Field>
              <FieldLabel htmlFor="background">Or describe your background</FieldLabel>
              <textarea
                className="min-h-24 w-full rounded-md border border-input bg-card px-3 py-2 text-sm"
                id="background"
                placeholder="A few sentences about what you have done: 'I led…', 'I built…', 'I closed…'"
                value={background}
                onChange={(event) => setBackground(event.target.value)}
                maxLength={20000}
              />
              <FieldDescription>
                Used with or instead of a resume. Follow-ups are built from these statements.
              </FieldDescription>
            </Field>

            </section>
            <section className="setup-section" aria-labelledby="role-heading">
              <SectionHeading number="02" id="role-heading" title="Your next role" description="The interview follows your profession and experience level." />
            <Field>
              <FieldLabel>Target role</FieldLabel>
              <Input
                placeholder="e.g. Backend engineer, Account executive"
                value={targetRole}
                onChange={(event) => setTargetRole(event.target.value)}
                maxLength={120}
              />
            </Field>

            <div className="field-grid">
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

            </div>
            </section>
            <section className="setup-section" aria-labelledby="conversation-heading">
              <SectionHeading number="03" id="conversation-heading" title="The conversation" description="Choose a full interview or focus on one round." />
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
                <RadioPrimitive.Root className={itemClassName} value="voice" disabled={!voiceAvailable} aria-describedby="voice-status">
                  Speak
                </RadioPrimitive.Root>
                <RadioPrimitive.Root className={itemClassName} value="text">
                  Type
                </RadioPrimitive.Root>
              </RadioGroupPrimitive>
              <FieldDescription>
                {lane === "text"
                  ? "Typing is fine. Spoken delivery is not assessed or scored."
                  : "You will need microphone access. You can switch to typing if voice fails."}
              </FieldDescription>
              {/* Shown whichever lane is selected, so a disabled Speak always says why. */}
              <p id="voice-status" className="voice-status" data-voice-state={voice.kind} aria-live="polite">
                {voiceMessage(voice)}
                {voice.kind === "unreachable" && (
                  <> <button type="button" className="voice-status-action" onClick={loadHealth}>Check again</button></>
                )}
              </p>
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

            </section>
            <details className="setup-optional">
              <summary><FilePlusIcon size={16} aria-hidden="true" /> Add more context <span className="text-xs font-normal text-muted-foreground">Optional</span><ChevronDownIcon size={16} aria-hidden="true" /></summary>
              <div className="optional-fields">
            <Field>
              <FieldLabel htmlFor="job-description">Job description (optional)</FieldLabel>
              <textarea
                className="min-h-20 w-full rounded-md border border-input bg-card px-3 py-2 text-sm"
                id="job-description"
                placeholder="Paste the job description, ideally including its requirements list."
                value={jobDescription}
                onChange={(event) => setJobDescription(event.target.value)}
                maxLength={20000}
              />
              <FieldDescription>Used for preparation guidance on terms your background does not mention.</FieldDescription>
            </Field>

            <Field>
              <FieldLabel htmlFor="company-context">Company context (optional)</FieldLabel>
              <textarea
                className="min-h-16 w-full rounded-md border border-input bg-card px-3 py-2 text-sm"
                id="company-context"
                placeholder="What you know about the company. No research is done for you."
                value={company}
                onChange={(event) => setCompany(event.target.value)}
                maxLength={4000}
              />
            </Field>

            <Field>
              <FieldLabel>Repository (optional)</FieldLabel>
              <Input
                type="url"
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

              </div>
            </details>
            <div className="setup-final">
            <ConsentBlock
              checked={consent}
              onChange={setConsent}
              retentionDays={Number(health?.retention_days ?? 30)}
            />

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
              {submitting ? <><LoaderCircleIcon className="animate-spin" /> Preparing…</> : <>Prepare my interview <ArrowRightIcon /></>}
            </Button>
            </div>
          </Form>
        </CardPanel>

        <CardFooter>
          <div className="flex gap-1.5 text-xs text-muted-foreground">
            <CircleAlertIcon className="mt-px size-3 shrink-0" />
            <p>
              Practice only — nothing here screens or ranks you. Scores are experimental coaching
              indicators, not hiring predictions or judgements of truth.
            </p>
          </div>
        </CardFooter>
      </Card>
      <aside className="setup-summary" aria-label="Your interview summary">
        <p className="eyebrow">YOUR PRACTICE, AT A GLANCE</p>
        <h2>{targetRole.trim() || "Your next conversation"}</h2>
        <dl>
          <div><dt>Profession</dt><dd>{ROLE_FAMILIES.find((item) => item.value === family)?.label}</dd></div>
          <div><dt>Experience</dt><dd>{SENIORITIES.find((item) => item.value === seniority)?.label}</dd></div>
          <div><dt>Answer by</dt><dd>{lane === "voice" ? "Speaking" : "Typing"}</dd></div>
          <div><dt>Difficulty</dt><dd>{INTENSITIES.find((item) => item.value === intensity)?.label}</dd></div>
        </dl>
        <ol className="summary-rounds" aria-label="Selected rounds">
          {(round === "full" ? ROUNDS.filter((item) => item.value !== "full") : ROUNDS.filter((item) => item.value === round)).map((item, index) => <li key={item.value}><span>{String(index + 1).padStart(2, "0")}</span>{item.label}</li>)}
        </ol>
        <p>You’ll review the statements extracted from your background before the interview begins. Nothing is uploaded until you confirm and continue.</p>
        <Button variant="link" size="sm" className="mt-3 p-0" onClick={onOpenHistory}>Return to your history <ArrowRightIcon /></Button>
      </aside>
      </div>
    </div>
  );
}

function JourneySteps({ current }: { current: number }) {
  return <ol className="journey-stepper" aria-label="Preparation steps">{["Your background", "Review & prepare", "Interview"].map((label, index) => <li key={label} aria-current={current === index ? "step" : undefined}><span>{index + 1}</span>{label}</li>)}</ol>;
}

function SectionHeading({ number, id, title, description }: { number: string; id: string; title: string; description: string }) {
  return <div className="section-heading"><span>{number}</span><div><h2 id={id}>{title}</h2><p>{description}</p></div></div>;
}

// ---------------------------------------------------------------------------
// Consent
// ---------------------------------------------------------------------------

function ConsentBlock({
  checked,
  onChange,
  retentionDays,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  retentionDays: number;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-md border border-border px-3 py-3 text-xs text-muted-foreground">
      <p className="text-sm font-medium text-foreground">Your data, with your permission</p>
      <p>Background and answers are processed by Groq; voice uses Deepgram. Your guest key is saved only in this browser, with no recovery. {retentionDays > 0 ? `App data is deleted after ${retentionDays} days without activity.` : "App data is kept until you delete it."}</p>
      <details>
      <summary className="cursor-pointer py-2 font-medium text-primary">Read the processing and retention details</summary>
      <ul className="flex list-disc flex-col gap-1 pl-4">
        <li>
          Text from your resume, background, job description and answers is sent to <strong>Groq</strong> (an
          external AI provider) to run the interviewer and produce feedback.
        </li>
        <li>
          If you answer by voice, your audio is streamed to <strong>Deepgram</strong> for speech-to-text, and the
          interviewer&apos;s lines are sent there to be spoken.
        </li>
        <li>
          Those providers process data under their own terms and retention policies. This app cannot delete
          copies they hold.
        </li>
        <li>
          You are a <strong>guest</strong>: a private key stored in this browser is your only access. Clearing
          site data or switching device loses access, and there is no recovery.
        </li>
        <li>
          {retentionDays > 0
            ? `Your data here is deleted automatically after ${retentionDays} days without activity, or at any time with "Delete my data".`
            : `Your data here is kept until you use "Delete my data".`}
        </li>
      </ul>
      </details>
      <label className="flex items-start gap-2 text-sm text-foreground">
        <input
          type="checkbox"
          className="mt-1"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
        />
        I understand and agree to this processing for my practice session.
      </label>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Source review
// ---------------------------------------------------------------------------

function StatementReview({
  intakeId,
  claims,
  result,
  onSaved,
  onStart,
  onChangeSetup,
}: {
  intakeId: string;
  claims: IntakeClaim[];
  result: IntakeResult;
  onSaved: (result: IntakeResult) => void;
  onStart: () => void;
  onChangeSetup: () => void;
}) {
  const [decisions, setDecisions] = useState<Record<string, Decision>>(() =>
    Object.fromEntries(
      claims.map((claim) => [
        claim.id,
        { action: claim.review?.action ?? "keep", text: claim.review?.text ?? claim.text },
      ]),
    ),
  );
  const [objective, setObjective] = useState(result.review?.objective?.competency ?? "");
  const [note, setNote] = useState(result.review?.objective?.note ?? "");
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState("");
  const objectives = Object.entries(result.objectives ?? {});

  const set = (id: string, update: Partial<Decision>) =>
    setDecisions((current) => ({ ...current, [id]: { ...current[id], ...update } }));

  const save = async (): Promise<boolean> => {
    setSaving(true);
    setProblem("");
    try {
      const next = await api<IntakeResult>(
        `/api/intake/${intakeId}/review`,
        jsonBody(
          {
            statements: claims.map((claim) => ({
              id: claim.id,
              action: decisions[claim.id].action,
              ...(decisions[claim.id].action === "edit" ? { text: decisions[claim.id].text } : {}),
            })),
            objective: objective ? { competency: objective, note } : null,
          },
          "PUT",
        ),
      );
      onSaved(next);
      return true;
    } catch (err) {
      setProblem((err as Error).message);
      return false;
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Check what the interviewers will ask about</CardTitle>
          <CardDescription>
            Each statement is shown where it came from. Keep it, correct what you meant, or exclude it. A
            correction is used as your own statement — it is never presented as a quote from your file, and
            the original stays on record. {result.evidence_note}
          </CardDescription>
        </CardHeader>
        <CardPanel className="flex flex-col gap-4">
          {claims.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No specific statements were found. Follow-ups will anchor on what you say.
            </p>
          )}
          {claims.map((claim) => {
            const decision = decisions[claim.id];
            const span = claim.source_span;
            return (
              <div key={claim.id} className="flex flex-col gap-2 border-b border-border pb-3 text-sm last:border-b-0">
                <p className="flex flex-wrap items-center gap-2">
                  <span className={decision.action === "exclude" ? "line-through text-muted-foreground" : ""}>“{claim.text}”</span>
                  <Badge variant="muted" size="sm">
                    {EVIDENCE_LABEL[claim.evidence_kind] ?? claim.evidence_kind}
                    {claim.evidence_kind === "repository" ? ` · ${claim.source_path}` : ""}
                  </Badge>
                </p>
                <p className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">
                  Source ({claim.source}):{" "}
                  {span?.found ? (
                    <>
                      …{span.before}
                      <mark className="bg-secondary/60 text-foreground">{span.match}</mark>
                      {span.after}…
                    </>
                  ) : (
                    <>“{claim.quote ?? claim.text}”</>
                  )}
                </p>
                <div className="flex flex-wrap gap-1" role="group" aria-label="What to do with this statement">
                  {(["keep", "edit", "exclude"] as ReviewAction[]).map((action) => (
                    <Button
                      key={action}
                      size="sm"
                      variant={decision.action === action ? "secondary" : "ghost"}
                      aria-pressed={decision.action === action}
                      onClick={() => set(claim.id, { action })}
                    >
                      {action === "keep" ? "Keep" : action === "edit" ? "Correct the meaning" : "Exclude"}
                    </Button>
                  ))}
                </div>
                {decision.action === "edit" && (
                  <div className="flex flex-col gap-1">
                    <textarea
                      className="min-h-16 w-full rounded-md border border-input bg-card px-3 py-2 text-sm"
                      maxLength={300}
                      value={decision.text}
                      onChange={(event) => set(claim.id, { text: event.target.value })}
                      aria-label="What you meant"
                    />
                    <p className="text-xs text-muted-foreground">
                      Your correction — used as your own statement, not as a quote from your file. Only write what
                      is true.
                    </p>
                  </div>
                )}
              </div>
            );
          })}
        </CardPanel>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Practice focus (optional)</CardTitle>
          <CardDescription>
            Steers follow-up questions toward one area. Every core question is still asked, and time and
            follow-up limits are unchanged.
          </CardDescription>
        </CardHeader>
        <CardPanel className="flex flex-col gap-2">
          <Select
            aria-label="Practice focus"
            items={[{ label: "No particular focus", value: "" }, ...objectives.map(([value, label]) => ({ label, value }))]}
            value={objective}
            onChange={(event) => setObjective(event.target.value)}
          />
          {objective && (
            <Input
              placeholder="Optional note, e.g. the project you want to be asked about"
              maxLength={200}
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          )}
        </CardPanel>
      </Card>

      {problem && (
        <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{problem}</p>
      )}

      <div className="flex flex-wrap gap-2">
        <Button
          size="lg"
          disabled={saving}
          onClick={async () => {
            if (await save()) onStart();
          }}
        >
          {saving ? "Saving…" : "Save and start the interview"}
        </Button>
        <Button variant="outline" disabled={saving} onClick={() => void save()}>
          Save review
        </Button>
        <Button variant="ghost" onClick={onChangeSetup}>
          Change setup
        </Button>
      </div>
    </>
  );
}
