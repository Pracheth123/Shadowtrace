import { STAGES, type StageIndex } from "./story-data";

// The four story steps as one ordered list. The current step expands to show its
// sentence. It is a list of buttons, not a live region, so scrolling through the
// story does not announce anything; each button jumps to its step.
export function StoryCaption({ active, onSelect, label }: { active: StageIndex; onSelect: (stage: StageIndex) => void; label: string }) {
  return (
    <ol className="story-steps" aria-label={label}>
      {STAGES.map((stage, index) => (
        <li key={stage.short} data-active={index === active ? "true" : "false"}>
          <button type="button" aria-current={index === active ? "step" : undefined} onClick={() => onSelect(index as StageIndex)}>
            <span className="story-step-number">{String(index + 1).padStart(2, "0")}</span>
            <span className="story-step-text">
              <span className="story-step-title">{stage.title}</span>
              <span className="story-step-body">{stage.text}</span>
            </span>
          </button>
        </li>
      ))}
    </ol>
  );
}
