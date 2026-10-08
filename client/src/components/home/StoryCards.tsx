import { useId, type FocusEventHandler, type KeyboardEvent } from "react";
import { ArrowRightIcon, QuoteIcon } from "lucide-react";
import { BACKGROUND_STATEMENT, STORY_ROLES, type StoryRole } from "./story-data";

function AnswerText({ role }: { role: StoryRole }) {
  const at = role.answer.indexOf(role.quote);
  if (at < 0) return <>{role.answer}</>;
  return <>{role.answer.slice(0, at)}<mark>{role.quote}</mark>{role.answer.slice(at + role.quote.length)}</>;
}

/** Interviewer card with semantic tabs: arrows, Home/End, roving tabindex, tab↔panel links. */
export function InterviewCard({ selected, onSelect, onFocusWithin }: { selected: number; onSelect: (index: number) => void; onFocusWithin?: FocusEventHandler }) {
  const id = useId();
  const role = STORY_ROLES[selected]!;
  const onKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const last = STORY_ROLES.length - 1;
    const next = event.key === "Home" ? 0 : event.key === "End" ? last : (selected + (event.key === "ArrowRight" ? 1 : last)) % STORY_ROLES.length;
    onSelect(next);
    document.getElementById(`${id}-tab-${next}`)?.focus();
  };
  return (
    <div className="story-card story-interview" onFocus={onFocusWithin}>
      <p className="story-card-kicker">Interview · same background, three rounds</p>
      <div className="story-tabs" role="tablist" aria-label="Example interviewer rounds">
        {STORY_ROLES.map((item, index) => (
          <button key={item.id} type="button" role="tab" id={`${id}-tab-${index}`} aria-controls={`${id}-panel`} aria-selected={index === selected} tabIndex={index === selected ? 0 : -1} onClick={() => onSelect(index)} onKeyDown={onKey}>
            {item.role}
          </button>
        ))}
      </div>
      <div className="story-panel" role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-tab-${selected}`}>
        <p className="story-label">From the background</p>
        <p className="story-source">“{BACKGROUND_STATEMENT}”</p>
        <p className="story-label">{role.role} interviewer asks</p>
        <p className="story-question" data-anchor="question">“{role.question}”</p>
        <p className="story-label">Example answer</p>
        <p className="story-answer">“<AnswerText role={role} />”</p>
      </div>
    </div>
  );
}

/** Coaching card. Quotes the example candidate answer, never the resume; no scores. */
export function FeedbackCard({ selected }: { selected: number }) {
  const role = STORY_ROLES[selected]!;
  return (
    <div className="story-card story-feedback">
      <p className="story-card-kicker"><QuoteIcon size={13} aria-hidden="true" /> Feedback · quoted from the answer</p>
      <blockquote className="story-quote">“{role.quote}”</blockquote>
      <p className="story-coaching"><strong>Coaching point:</strong> {role.coaching}</p>
      <p className="story-next"><ArrowRightIcon size={13} aria-hidden="true" /> Next: a short practice on this answer</p>
    </div>
  );
}
