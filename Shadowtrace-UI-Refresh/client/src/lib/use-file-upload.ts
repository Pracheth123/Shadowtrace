import { useCallback, useId, useRef, useState } from "react";

/**
 * File-upload hook, ported from the Origin UI Angular example to React.
 *
 * Kept to the same two-part shape as the original — `[state, actions]` — so the
 * saved snippet reads the same way here. Changes from the original:
 *
 *   - no `preview` object URLs. The original previewed an image; a resume is a
 *     PDF or DOCX, so there is nothing to show and an object URL would just be
 *     a leak to revoke.
 *   - validation reports *why* a file was rejected (type vs size), because
 *     "upload failed" tells the candidate nothing about what to do next.
 */

export type UploadedFile = {
  id: string;
  file: File;
};

export type UseFileUploadOptions = {
  /** Comma-separated accept list, e.g. ".pdf,.docx". */
  accept?: string;
  /** Max bytes per file. */
  maxSize?: number;
  multiple?: boolean;
  onFilesChange?: (files: UploadedFile[]) => void;
};

export type FileUploadState = {
  files: UploadedFile[];
  isDragging: boolean;
  errors: string[];
};

function formatBytes(bytes: number) {
  if (bytes >= 1024 * 1024) return `${Math.round(bytes / (1024 * 1024))}MB`;
  return `${Math.round(bytes / 1024)}KB`;
}

function matchesAccept(file: File, accept: string) {
  if (!accept.trim()) return true;
  const name = file.name.toLowerCase();
  return accept.split(",").some((raw) => {
    const token = raw.trim().toLowerCase();
    if (!token) return false;
    if (token.startsWith(".")) return name.endsWith(token);
    if (token.endsWith("/*")) return file.type.startsWith(token.slice(0, -1));
    return file.type === token;
  });
}

export function useFileUpload({
  accept = "",
  maxSize = Number.POSITIVE_INFINITY,
  multiple = false,
  onFilesChange,
}: UseFileUploadOptions = {}) {
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const idPrefix = useId();
  const counter = useRef(0);
  // Nested dragenter/dragleave events fire for child elements too; count them
  // so the drop zone does not flicker while the cursor crosses the icon.
  const dragDepth = useRef(0);

  const commit = useCallback(
    (next: UploadedFile[]) => {
      setFiles(next);
      onFilesChange?.(next);
    },
    [onFilesChange],
  );

  const accepted = useCallback(
    (incoming: FileList | File[]) => {
      const problems: string[] = [];
      const ok: UploadedFile[] = [];
      for (const file of Array.from(incoming)) {
        if (!matchesAccept(file, accept)) {
          problems.push(`${file.name} is not an accepted file type.`);
          continue;
        }
        if (file.size > maxSize) {
          problems.push(
            `${file.name} is ${formatBytes(file.size)}; the limit is ${formatBytes(maxSize)}.`,
          );
          continue;
        }
        ok.push({ id: `${idPrefix}-${counter.current++}`, file });
      }
      setErrors(problems);
      return ok;
    },
    [accept, maxSize, idPrefix],
  );

  const addFiles = useCallback(
    (incoming: FileList | File[]) => {
      const ok = accepted(incoming);
      if (ok.length === 0) return;
      commit(multiple ? [...files, ...ok] : ok.slice(0, 1));
    },
    [accepted, commit, files, multiple],
  );

  const actions = {
    openFileDialog: () => inputRef.current?.click(),
    removeFile: (id: string) => {
      commit(files.filter((item) => item.id !== id));
      setErrors([]);
      // Clear the input so re-picking the same file still fires onChange.
      if (inputRef.current) inputRef.current.value = "";
    },
    clearErrors: () => setErrors([]),
    handleDragEnter: (event: React.DragEvent) => {
      event.preventDefault();
      dragDepth.current += 1;
      setIsDragging(true);
    },
    handleDragLeave: (event: React.DragEvent) => {
      event.preventDefault();
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (dragDepth.current === 0) setIsDragging(false);
    },
    handleDragOver: (event: React.DragEvent) => {
      event.preventDefault();
    },
    handleDrop: (event: React.DragEvent) => {
      event.preventDefault();
      dragDepth.current = 0;
      setIsDragging(false);
      if (event.dataTransfer?.files?.length) addFiles(event.dataTransfer.files);
    },
    getInputProps: () => ({
      ref: inputRef,
      accept,
      multiple,
      type: "file" as const,
      onChange: (event: React.ChangeEvent<HTMLInputElement>) => {
        if (event.target.files?.length) addFiles(event.target.files);
      },
    }),
  };

  const state: FileUploadState = { files, isDragging, errors };

  return [state, actions] as const;
}
