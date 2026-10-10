// Whether "Speak" can be offered, and why not. Pure, so every state can be
// exercised by the browser checks with a stubbed /health.
//
// `providers.voice === "deepgram"` only means a DEEPGRAM_API_KEY is present on
// the server: configuration, not proof that speech works. `verified.deepgram`
// is the result of the last authenticated check (POST /api/diagnostics/verify),
// or null when none has run. A known-failed check disables Speak; an unknown
// one leaves it on, and the room still offers typing if voice fails.

export type MicPermission = "granted" | "denied" | "prompt" | "unknown";

export interface BrowserVoiceSupport {
  captureSupported: boolean;
  secureContext: boolean;
  micPermission: MicPermission;
}

export type VoiceState =
  | { kind: "checking" }
  | { kind: "unreachable" }
  | { kind: "not_configured" }
  | { kind: "provider_failed" }
  | { kind: "browser_unsupported"; insecure: boolean }
  | { kind: "mic_blocked" }
  | { kind: "available"; verified: boolean | null };

interface HealthLike {
  providers?: { voice?: string; verified?: { deepgram?: boolean | null } };
}

export function voiceState(health: HealthLike | null, healthFailed: boolean, browser: BrowserVoiceSupport): VoiceState {
  if (!health) return healthFailed ? { kind: "unreachable" } : { kind: "checking" };
  if (health.providers?.voice !== "deepgram") return { kind: "not_configured" };
  const verified = health.providers?.verified?.deepgram ?? null;
  if (verified === false) return { kind: "provider_failed" };
  if (!browser.secureContext || !browser.captureSupported) return { kind: "browser_unsupported", insecure: !browser.secureContext };
  if (browser.micPermission === "denied") return { kind: "mic_blocked" };
  return { kind: "available", verified };
}

export const canSpeak = (state: VoiceState) => state.kind === "available";

/** Candidate-facing explanation. Always names the way forward. */
export function voiceMessage(state: VoiceState): string {
  switch (state.kind) {
    case "checking":
      return "Checking whether voice is available…";
    case "unreachable":
      return "We can’t reach the interview service, so voice can’t be checked. Check again, or reload the page.";
    case "not_configured":
      return "Voice isn’t available on this server right now. Choose Type: the interview and feedback work the same, and spoken delivery is not assessed.";
    case "provider_failed":
      return "Voice is switched off because the speech service did not accept the server’s last check. Choose Type to continue.";
    case "browser_unsupported":
      return state.insecure
        ? "Voice needs a secure (https) connection, which this page doesn’t have. Choose Type to continue."
        : "This browser can’t record audio for the interview. Choose Type, or try a current Chrome, Edge, Firefox or Safari.";
    case "mic_blocked":
      return "Microphone access is blocked for this site. Allow it in your browser’s site settings to use voice, or choose Type.";
    case "available":
      return "Voice is available. You will need microphone access, and you can switch to typing at any time.";
  }
}

/**
 * Operator guidance, shown only in a development build. Key setup belongs in
 * diagnostics and the run documentation, never in candidate-facing copy.
 */
export function voiceDeveloperNote(state: VoiceState): string | null {
  switch (state.kind) {
    case "unreachable":
      return "Start the backend: python -m uvicorn interview.server:app --host 127.0.0.1 --port 8000";
    case "not_configured":
      return "Set DEEPGRAM_API_KEY in the server’s .env and restart the backend (README → Configuration).";
    case "provider_failed":
      return "Run python tools/check_providers.py or POST /api/diagnostics/verify to see the provider’s error.";
    case "available":
      return state.verified === null ? "Speech credentials are configured but not yet verified (POST /api/diagnostics/verify)." : null;
    default:
      return null;
  }
}

/** Current microphone permission without prompting. "unknown" where the API is missing. */
export async function readMicPermission(): Promise<{ state: MicPermission; status: PermissionStatus | null }> {
  try {
    const status = await navigator.permissions?.query({ name: "microphone" as PermissionName });
    if (!status) return { state: "unknown", status: null };
    return { state: status.state as MicPermission, status };
  } catch {
    return { state: "unknown", status: null };
  }
}
