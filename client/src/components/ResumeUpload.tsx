import {
  AlertCircleIcon,
  FileTextIcon,
  UploadIcon,
  XIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { useFieldId } from "@/components/ui/field";
import { UPLOAD_ACCEPT, UPLOAD_LABEL, UPLOAD_MAX_MB } from "@/lib/api";
import { useFileUpload } from "@/lib/use-file-upload";
import { cn } from "@/lib/utils";

// Same list and limit the server enforces (intake/documents.py). The server
// also checks the content, so a renamed file is still rejected there.
const MAX_SIZE_MB = UPLOAD_MAX_MB;
const MAX_SIZE = MAX_SIZE_MB * 1024 * 1024;
const ACCEPT = UPLOAD_ACCEPT;

function formatSize(bytes: number) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export type ResumeUploadProps = {
  onFileChange?: (file: File | null) => void;
  /** What the drop zone is for, e.g. "resume" or "work sample". */
  noun?: string;
};

/**
 * Resume drop zone — the Origin UI file upload, adapted from images to
 * documents.
 *
 * A PDF has no thumbnail, so where the image version showed a preview this
 * shows the filename and size: the thing a candidate actually needs to
 * confirm they picked the right file.
 */
export function ResumeUpload({ onFileChange, noun = "resume" }: ResumeUploadProps) {
  const fieldId = useFieldId();
  const [state, actions] = useFileUpload({
    accept: ACCEPT,
    maxSize: MAX_SIZE,
    onFilesChange: (files) => onFileChange?.(files[0]?.file ?? null),
  });

  const { ref, onChange, ...inputProps } = actions.getInputProps();
  const picked = state.files[0];

  return (
    <div className="flex flex-col gap-2">
      <div
        data-dragging={state.isDragging || undefined}
        onDragEnter={actions.handleDragEnter}
        onDragLeave={actions.handleDragLeave}
        onDragOver={actions.handleDragOver}
        onDrop={actions.handleDrop}
        className={cn(
          "relative flex min-h-44 flex-col items-center justify-center overflow-hidden rounded-lg border border-dashed border-input p-4 transition-colors",
          "has-[input:focus]:border-ring has-[input:focus]:ring-2 has-[input:focus]:ring-ring/35",
          "data-[dragging=true]:border-accent data-[dragging=true]:bg-accent/5",
        )}
      >
        <input
          id={fieldId ?? undefined}
          ref={ref}
          onChange={onChange}
          className="sr-only"
          aria-label={`Upload ${noun} file`}
          {...inputProps}
        />

        {picked ? (
          <div className="flex w-full items-center gap-3 px-2">
            <div
              aria-hidden="true"
              className="flex size-11 shrink-0 items-center justify-center rounded-full border border-border bg-background"
            >
              <FileTextIcon className="size-4 text-primary" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium" title={picked.file.name}>
                {picked.file.name}
              </p>
              <p className="text-xs text-muted-foreground">
                {formatSize(picked.file.size)} · read when you continue
              </p>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => actions.removeFile(picked.id)}
              aria-label={`Remove ${picked.file.name}`}
            >
              <XIcon />
              Remove
            </Button>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center px-4 py-3 text-center">
            <div
              aria-hidden="true"
              className="mb-2 flex size-11 shrink-0 items-center justify-center rounded-full border border-border bg-background"
            >
              <FileTextIcon className="size-4 text-muted-foreground" />
            </div>
            <p className="mb-1 text-sm font-medium">Drop your {noun} here</p>
            <p className="text-xs text-muted-foreground">
              {UPLOAD_LABEL} (max. {MAX_SIZE_MB} MB)
            </p>
            <Button
              variant="outline"
              className="mt-4"
              onClick={actions.openFileDialog}
            >
              <UploadIcon className="opacity-60" />
              Select {noun}
            </Button>
          </div>
        )}
      </div>

      {state.errors.length > 0 && (
        <div
          role="alert"
          className="flex items-start gap-1.5 text-xs text-destructive"
        >
          <AlertCircleIcon className="mt-px size-3 shrink-0" />
          <span>{state.errors[0]}</span>
        </div>
      )}

    </div>
  );
}
