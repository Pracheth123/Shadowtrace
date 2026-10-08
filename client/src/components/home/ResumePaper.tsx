import type { CSSProperties } from "react";
import { m, type MotionValue } from "motion/react";
import { BACKGROUND_STATEMENT, SECOND_STATEMENT, type PaperKind } from "./story-data";

// Original paper artwork in HTML/CSS: warm sheet, folded corner, a readable
// heading and abstract text lines. Generic content, no personal information.
// Decorative: always rendered inside an aria-hidden layer.

const Lines = ({ widths }: { widths: number[] }) => <span className="paper-lines">{widths.map((w, i) => <i key={i} style={{ width: `${w}%` }} />)}</span>;

function Highlight({ text, reveal }: { text: string; reveal?: MotionValue<number> }) {
  return (
    <span className="paper-statement">
      {reveal ? <m.span className="paper-highlight" style={{ scaleX: reveal }} /> : <span className="paper-highlight" />}
      <span className="paper-statement-text">{text}</span>
    </span>
  );
}

export function ResumePaper({ kind, highlights, style }: { kind: PaperKind; highlights?: [MotionValue<number>, MotionValue<number>]; style?: CSSProperties }) {
  return (
    <div className={`paper paper-${kind}`} style={style}>
      <span className="paper-fold" />
      {kind === "resume" && <>
        <span className="paper-head"><span className="paper-avatar" /><Lines widths={[62, 38]} /></span>
        <span className="paper-heading">Experience</span>
        <Highlight text={BACKGROUND_STATEMENT} reveal={highlights?.[0]} />
        <Lines widths={[88, 72]} />
        <Highlight text={SECOND_STATEMENT} reveal={highlights?.[1]} />
        <span className="paper-heading">Skills</span>
        <Lines widths={[80, 56]} />
      </>}
      {kind === "projects" && <>
        <span className="paper-heading">Projects</span>
        <span className="paper-block" />
        <Lines widths={[90, 76, 84]} />
        <span className="paper-block paper-block-small" />
        <Lines widths={[70, 86]} />
      </>}
      {kind === "skills" && <>
        <span className="paper-heading">Skills</span>
        <span className="paper-chips">{[46, 30, 38, 52, 34, 40].map((w, i) => <i key={i} style={{ width: `${w}%` }} />)}</span>
        <Lines widths={[84, 62]} />
      </>}
      {kind === "experience" && <>
        <span className="paper-heading">Work experience</span>
        {[0, 1, 2].map((i) => <span key={i} className="paper-entry"><b /><Lines widths={[78 - i * 8, 58 + i * 6]} /></span>)}
      </>}
      {kind === "sample" && <>
        <span className="paper-heading">Work sample</span>
        <span className="paper-code">{[30, 64, 52, 78, 40, 58, 70, 34].map((w, i) => <i key={i} style={{ width: `${w}%`, marginLeft: `${(i % 3) * 7}%` }} />)}</span>
      </>}
    </div>
  );
}
