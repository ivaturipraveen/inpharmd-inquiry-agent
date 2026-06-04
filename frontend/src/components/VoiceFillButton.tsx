import { FC, useCallback, useEffect, useRef, useState } from "react";
import { Conversation } from "@elevenlabs/client";
import type { ClientTools } from "@elevenlabs/react";
import { api } from "../api";

type DynamicVariables = Record<string, string | number | boolean>;

interface Props {
  /** Label shown next to the mic icon when idle. */
  label?: string;
  /** Client tools the agent can call to mutate the form. */
  clientTools: ClientTools;
  /** Context the agent needs (e.g. form_type, available_manufacturers). */
  dynamicVariables?: DynamicVariables;
  /** Override the agent's first message. Optional. */
  firstMessage?: string;
  /** Fires when the session ends (any reason). */
  onEnd?: () => void;
}

type Status = "idle" | "connecting" | "listening" | "speaking" | "error";

const VoiceFillButton: FC<Props> = ({
  label = "Fill with voice",
  clientTools,
  dynamicVariables,
  firstMessage,
  onEnd,
}) => {
  const [status, setStatus] = useState<Status>("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const conversationRef = useRef<Conversation | null>(null);

  // Keep the latest tool handlers in a ref so they aren't stale when the
  // SDK invokes them — React form state updates between session start and
  // tool calls.
  const toolsRef = useRef(clientTools);
  useEffect(() => {
    toolsRef.current = clientTools;
  }, [clientTools]);

  const stop = useCallback(async () => {
    const conv = conversationRef.current;
    conversationRef.current = null;
    if (conv) {
      try {
        await conv.endSession();
      } catch {
        /* ignore — already gone */
      }
    }
    setStatus("idle");
  }, []);

  // Make sure we never leave a session open when the modal unmounts.
  useEffect(() => {
    return () => {
      stop();
    };
  }, [stop]);

  const start = useCallback(async () => {
    setErrorMsg(null);
    setStatus("connecting");
    try {
      // Browser will prompt for mic permission here if not yet granted.
      await navigator.mediaDevices.getUserMedia({ audio: true });

      const { signed_url } = await api.voice.signedUrl();

      // Wrap each tool handler so we always call the latest version from
      // the ref (handlers close over React state).
      const wrapped: ClientTools = Object.fromEntries(
        Object.keys(clientTools).map((name) => [
          name,
          async (params: Record<string, unknown>) => {
            const fn = toolsRef.current[name];
            if (!fn) return `Tool "${name}" not registered.`;
            try {
              const result = await fn(params);
              return result ?? "ok";
            } catch (err: any) {
              return `Error: ${err?.message ?? String(err)}`;
            }
          },
        ])
      );

      const conv = await Conversation.startSession({
        signedUrl: signed_url,
        dynamicVariables,
        clientTools: wrapped,
        ...(firstMessage
          ? { overrides: { agent: { firstMessage } } }
          : {}),
        onStatusChange: ({ status: s }) => {
          if (s === "connected") setStatus("listening");
          else if (s === "disconnected") {
            setStatus("idle");
            onEnd?.();
          }
        },
        onModeChange: ({ mode }) => {
          setStatus(mode === "speaking" ? "speaking" : "listening");
        },
        onError: (message: string) => {
          setErrorMsg(message);
          setStatus("error");
        },
      });
      conversationRef.current = conv;
    } catch (err: any) {
      setErrorMsg(err?.message ?? "Could not start voice session.");
      setStatus("error");
      conversationRef.current = null;
    }
  }, [clientTools, dynamicVariables, firstMessage, onEnd]);

  const onClick = () => {
    if (status === "idle" || status === "error") {
      start();
    } else {
      stop();
    }
  };

  const isActive = status === "listening" || status === "speaking";
  const cls = [
    "voice-btn",
    isActive ? "voice-btn-active" : "",
    status === "connecting" ? "voice-btn-loading" : "",
    status === "error" ? "voice-btn-error" : "",
  ]
    .filter(Boolean)
    .join(" ");

  const displayLabel =
    status === "connecting"
      ? "Connecting…"
      : status === "listening"
      ? "Listening… (tap to stop)"
      : status === "speaking"
      ? "Speaking… (tap to stop)"
      : status === "error"
      ? "Retry voice"
      : label;

  return (
    <div className="voice-btn-wrap">
      <button
        type="button"
        className={cls}
        onClick={onClick}
        title={errorMsg ?? displayLabel}
      >
        <svg
          className="voice-btn-icon"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <rect x="9" y="3" width="6" height="12" rx="3" />
          <path d="M5 11a7 7 0 0 0 14 0" />
          <line x1="12" y1="18" x2="12" y2="22" />
          <line x1="8" y1="22" x2="16" y2="22" />
        </svg>
        <span>{displayLabel}</span>
        {isActive && <span className="voice-btn-pulse" aria-hidden />}
      </button>
      {errorMsg && status === "error" && (
        <div className="voice-btn-error-text">{errorMsg}</div>
      )}
    </div>
  );
};

export default VoiceFillButton;
