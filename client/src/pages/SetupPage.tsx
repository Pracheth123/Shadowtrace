import { useState } from "react";
import { CircleAlertIcon } from "lucide-react";

import { ResumeUpload } from "@/components/ResumeUpload";
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
import { Select } from "@/components/ui/select";
import {
  RadioGroupPrimitive,
  RadioPrimitive,
} from "@/components/ui/radio-group";
import { toastManager } from "@/components/ui/toast";
import {
  segmentedControlItemVariants,
  segmentedControlRootClassName,
} from "@/lib/segmented-control";
import type { Intensity, Lane, SessionOptions } from "@/lib/use-session";

const itemClassName = segmentedControlItemVariants({
  className: "grow",
  state: "checked",
});

const PACKS = [
  { label: "Behavioural core", value: "behavioral-core" },
  { label: "Systems design", value: "systems-design" },
  { label: "Public company", value: "public-company" },
];

const INTENSITIES: { label: string; value: Intensity; hint: string }[] = [
  { label: "Coach", value: "coach", hint: "Hints offered, shallow follow-ups." },
  {
    label: "Realistic",
    value: "realistic",
    hint: "No hints. Follow-ups go two deep.",
  },
  {
    label: "Panel",
    value: "panel",
    hint: "Hardest follow-ups. Multiple voices if panel mode is on.",
  },
];

export type SetupPageProps = {
  onStart: (options: SessionOptions) => void;
};

export function SetupPage({ onStart }: SetupPageProps) {
  const [mode, setMode] = useState("practice");
  const [lane, setLane] = useState<Lane>("voice");
  const [intensity, setIntensity] = useState<Intensity>("realistic");
  const [packId, setPackId] = useState("behavioral-core");
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [resume, setResume] = useState<File | null>(null);

  // A mock interview is the graded run, so it starts at realistic and the
  // panel roster is in the room. Practice stays gentler by default.
  const chooseMode = (next: string) => {
    setMode(next);
    setIntensity(next === "mock" ? "panel" : "coach");
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!resume) {
      toastManager.add({
        title: "Add your resume first",
        description: "Questions are built from your own resume and repo.",
        tone: "error",
      });
      return;
    }
    toastManager.add({
      title: "Interview saved",
      description: `${mode === "mock" ? "Mock interview" : "Practice"} · ${intensity}`,
      tone: "success",
    });
    onStart({ lane, intensity, panelMode: mode === "mock", packId });
  };

  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1>Panel AI</h1>
        <p className="text-sm text-muted-foreground">
          A mock interview built from your own resume and repository — not a
          question bank. One voice at a time, scored on four dimensions.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Set up your interview</CardTitle>
          <CardDescription>
            Takes about a minute. You can stop at any point.
          </CardDescription>
        </CardHeader>

        <CardPanel>
          <Form className="w-full" onSubmit={submit}>
            <Field>
              <FieldLabel>Your resume</FieldLabel>
              <ResumeUpload onFileChange={setResume} />
            </Field>

            <Field>
              <FieldLabel>Name</FieldLabel>
              <Input
                placeholder="Your name"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </Field>

            <Field>
              <FieldLabel>Company you are preparing for</FieldLabel>
              <Input
                placeholder="Company name"
                value={company}
                onChange={(event) => setCompany(event.target.value)}
              />
              <FieldDescription>
                Used to pick the question spine. Optional.
              </FieldDescription>
            </Field>

            <Field>
              <FieldLabel>Format</FieldLabel>
              <Select
                items={PACKS}
                value={packId}
                onChange={(event) => setPackId(event.target.value)}
              />
            </Field>

            <Field>
              <FieldLabel id="mode-label">Session type</FieldLabel>
              <RadioGroupPrimitive
                aria-labelledby="mode-label"
                className={segmentedControlRootClassName}
                value={mode}
                onValueChange={chooseMode}
                name="session-type"
              >
                <RadioPrimitive.Root className={itemClassName} value="practice">
                  Practice
                </RadioPrimitive.Root>
                <RadioPrimitive.Root className={itemClassName} value="mock">
                  Mock Interview
                </RadioPrimitive.Root>
              </RadioGroupPrimitive>
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
                  ? "Typing is fine. Delivery is not assessed, and nothing else changes."
                  : "You will need microphone access."}
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
                  <RadioPrimitive.Root
                    key={item.value}
                    className={itemClassName}
                    value={item.value}
                  >
                    {item.label}
                  </RadioPrimitive.Root>
                ))}
              </RadioGroupPrimitive>
              <FieldDescription>
                {INTENSITIES.find((item) => item.value === intensity)?.hint}
              </FieldDescription>
            </Field>

            <Button className="w-full" size="lg" type="submit">
              Start interview
            </Button>
          </Form>
        </CardPanel>

        <CardFooter>
          <div className="flex gap-1.5 text-xs text-muted-foreground">
            <CircleAlertIcon className="mt-px size-3 shrink-0" />
            <p>
              Nothing here screens or ranks you. Your resume is read to build
              questions and is never scored on its own.
            </p>
          </div>
        </CardFooter>
      </Card>
    </div>
  );
}
