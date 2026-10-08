import { useTransform, type MotionValue } from "motion/react";
import { HOLDS, type Pose, type Track } from "./story-data";

// Keyframe times: each stage holds, and every move between holds has a midpoint.
// At the midpoint x has travelled further than y (and vice versa on the second
// half), so straight moves become gently curved paths, the way a thrown sheet
// arcs rather than slides. Times are stable, so a given scroll position always
// produces the same composition, in either direction and at any scroll speed.
const TIMES: number[] = [];
HOLDS.forEach(([start, end], i) => {
  TIMES.push(start, end);
  const next = HOLDS[i + 1];
  if (next) TIMES.push((end + next[0]) / 2);
});
// [0, .08, .17, .26, .44, .50, .56, .72, .78, .84, 1]

const hold = (t: number) => t;
const easeIn = (t: number) => 1 - Math.cos((t * Math.PI) / 2);
const easeOut = (t: number) => Math.sin((t * Math.PI) / 2);
const EASES = TIMES.slice(1).map((_, i) => (i % 3 === 0 ? hold : i % 3 === 1 ? easeIn : easeOut));

/** Expand four poses into keyframes for one property. `lead` shapes the curve. */
function keyframes(track: Track, prop: number, lead: number): number[] {
  const out: number[] = [];
  track.forEach((pose, i) => {
    out.push(pose[prop]!, pose[prop]!);
    const next = track[i + 1];
    if (next) out.push(pose[prop]! + (next[prop]! - pose[prop]!) * lead);
  });
  return out;
}

export interface StageSize { w: MotionValue<number>; h: MotionValue<number> }

/** Motion values for one element on its own wrapper. No React state, no layout reads. */
export function usePose(progress: MotionValue<number>, size: StageSize, track: Track) {
  const fx = useTransform(progress, TIMES, keyframes(track, 0, 0.66), { ease: EASES });
  const fy = useTransform(progress, TIMES, keyframes(track, 1, 0.34), { ease: EASES });
  const rotate = useTransform(progress, TIMES, keyframes(track, 2, 0.5), { ease: EASES });
  const scale = useTransform(progress, TIMES, keyframes(track, 3, 0.5), { ease: EASES });
  const opacity = useTransform(progress, TIMES, keyframes(track, 4, 0.5), { ease: EASES });
  const x = useTransform([fx, size.w], ([f, w]: number[]) => f! * w!);
  const y = useTransform([fy, size.h], ([f, h]: number[]) => f! * h!);
  return { x, y, rotate, scale, opacity };
}

/** 0→1 over [a, b], clamped. */
export function useWindow(progress: MotionValue<number>, a: number, b: number) {
  return useTransform(progress, [a, b], [0, 1], { clamp: true });
}

export const poseAt = (track: Track, stage: number): Pose => track[stage as 0 | 1 | 2 | 3];
