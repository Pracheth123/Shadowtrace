import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Badge } from "@/components/ui/badge";

/**
 * Post-interview score chart.
 *
 * Two things worth knowing about the data, both of which drive the design:
 *
 *  1. The backend scores four fixed dimensions — technical, structure,
 *     delivery, competency — on 0–1. They are rescaled to 0–100 here only for
 *     display; the scale is fixed to [0, 100] so two sessions are visually
 *     comparable rather than each auto-scaling to its own max.
 *  2. A dimension can come back `assessed: false` — delivery does, for a
 *     text-lane session, where there is no spoken answer to assess. That is
 *     *not* a zero. Plotting it as a 0-height bar would make an unmeasured
 *     dimension read as the worst score on the report, so it is pulled out of
 *     the chart and named explicitly underneath.
 */

export type DimensionScore = {
  dimension: string;
  score: number; // 0–1 as the scorer emits it
  finding_count: number;
  assessed?: boolean;
};

export type ScoreChartProps = {
  dimensions: DimensionScore[];
  /** Optional previous session, drawn beside the current one. */
  previous?: DimensionScore[] | null;
  lane?: "voice" | "text";
};

const LABELS: Record<string, string> = {
  technical: "Technical substance",
  structure: "Structure of thinking",
  delivery: "Delivery",
  competency: "Competency alignment",
};

const PRIMARY = "#3d27ce";
const SECONDARY = "#ffdbed";
const GRID = "#e6e2ea";
const INK = "#021016";
const MUTED = "#5f5a6b";

function label(dimension: string) {
  return LABELS[dimension] ?? dimension;
}

export function ScoreChart({ dimensions, previous, lane }: ScoreChartProps) {
  const assessed = dimensions.filter((item) => item.assessed !== false);
  const notAssessed = dimensions.filter((item) => item.assessed === false);

  const previousByName = new Map(
    (previous ?? [])
      .filter((item) => item.assessed !== false)
      .map((item) => [item.dimension, item.score]),
  );

  const data = assessed.map((item) => ({
    label: label(item.dimension),
    current: Math.round(item.score * 100),
    previous: previousByName.has(item.dimension)
      ? Math.round((previousByName.get(item.dimension) ?? 0) * 100)
      : undefined,
    findings: item.finding_count,
  }));

  const hasPrevious = data.some((row) => row.previous !== undefined);

  if (data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No dimension was assessed in this session.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Fixed aspect ratio keeps the bars from stretching on wide screens. */}
      <div className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            margin={{ top: 8, right: 8, left: 0, bottom: 8 }}
            barCategoryGap="24%"
          >
            <CartesianGrid
              stroke={GRID}
              strokeDasharray="3 3"
              vertical={false}
            />
            <XAxis
              dataKey="label"
              tick={{ fill: MUTED, fontSize: 11 }}
              tickLine={false}
              axisLine={{ stroke: GRID }}
              interval={0}
              height={48}
              tickFormatter={(value: string) =>
                value.length > 16 ? `${value.slice(0, 15)}…` : value
              }
            />
            <YAxis
              domain={[0, 100]}
              ticks={[0, 25, 50, 75, 100]}
              tick={{ fill: MUTED, fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={32}
            />
            <Tooltip
              cursor={{ fill: "rgba(61, 39, 206, 0.06)" }}
              contentStyle={{
                background: "#ffffff",
                border: `1px solid ${GRID}`,
                borderRadius: 12,
                color: INK,
                fontSize: 12,
              }}
              formatter={(value: number, name) => [
                `${value} / 100`,
                name === "current" ? "This session" : "Previous session",
              ]}
            />
            {hasPrevious && (
              <Legend
                formatter={(value) =>
                  value === "current" ? "This session" : "Previous session"
                }
                wrapperStyle={{ fontSize: 12, color: MUTED }}
              />
            )}
            {hasPrevious && (
              <Bar
                dataKey="previous"
                name="previous"
                fill={SECONDARY}
                stroke={GRID}
                radius={[10, 10, 0, 0]}
              />
            )}
            <Bar dataKey="current" name="current" radius={[10, 10, 0, 0]}>
              {data.map((row) => (
                <Cell key={row.label} fill={PRIMARY} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {notAssessed.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-muted/60 p-3">
          {notAssessed.map((item) => (
            <Badge key={item.dimension} variant="muted" size="lg">
              {label(item.dimension)} — not assessed
            </Badge>
          ))}
          <p className="text-xs text-muted-foreground">
            {lane === "text"
              ? "This session ran in the text lane, which has no spoken answer to assess. It is not scored as zero."
              : "Not measured in this session. It is not scored as zero."}
          </p>
        </div>
      )}
    </div>
  );
}
