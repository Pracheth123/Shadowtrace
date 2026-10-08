import { Badge, BadgeDot } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

/**
 * Interview history — the saved Table, with interview content.
 *
 * Columns are Interview / Status / Date / Score as specified, the Total row is
 * gone, and each row ends in a View feedback button. A session with no score
 * yet shows an em dash rather than a 0, for the same reason the chart does not
 * plot an unassessed dimension.
 */

export type InterviewStatus =
  | "completed"
  | "processing"
  | "in_progress"
  | "failed";

export type InterviewRow = {
  id: string;
  interview: string;
  status: InterviewStatus;
  date: string;
  /** 0–100, or null when there is no report yet. */
  score: number | null;
  lane?: "voice" | "text";
};

const STATUS: Record<InterviewStatus, { label: string; dot: string }> = {
  completed: { label: "Completed", dot: "bg-success" },
  processing: { label: "Processing", dot: "bg-warning" },
  in_progress: { label: "In progress", dot: "bg-accent" },
  failed: { label: "Failed", dot: "bg-destructive" },
};

export type InterviewHistoryProps = {
  rows: InterviewRow[];
  onViewFeedback?: (row: InterviewRow) => void;
};

export function InterviewHistory({
  rows,
  onViewFeedback,
}: InterviewHistoryProps) {
  if (rows.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No interviews yet. Your first session will appear here.
      </p>
    );
  }

  return (
    <Table>
      <TableCaption>
        Every session you have run. Scores are only compared between sessions
        with the same profession, round selection and answer mode.
      </TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>Interview</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Date</TableHead>
          <TableHead className="text-right">Score</TableHead>
          <TableHead className="sr-only">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => {
          const status = STATUS[row.status];
          return (
            <TableRow key={row.id}>
              <TableCell className="font-medium">
                {row.interview}
                {row.lane === "text" && (
                  <Badge variant="muted" size="sm" className="ml-2">
                    text lane
                  </Badge>
                )}
              </TableCell>
              <TableCell>
                <Badge variant="outline">
                  <BadgeDot className={cn(status.dot)} />
                  {status.label}
                </Badge>
              </TableCell>
              <TableCell className="text-muted-foreground">
                {row.date}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {row.score === null ? (
                  <span className="text-muted-foreground">—</span>
                ) : (
                  <>
                    {row.score}
                    <span className="text-muted-foreground">/100</span>
                  </>
                )}
              </TableCell>
              <TableCell className="text-right">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={row.status === "in_progress"}
                  onClick={() => onViewFeedback?.(row)}
                >
                  {row.status === "completed" ? "View feedback" : "View status"}
                </Button>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
