export type TriggerChannel = "webform" | "email" | "call";

/** Order-independent so Select All/Select None/toggle produce a stable id. */
export function selectionSignatureFromKeys(keys: Iterable<string>): string {
  return Array.from(keys).sort().join(",");
}

/** Tracks which channels already succeeded for the CURRENT selection only —
 *  any signature change (a toggled/added/removed row) resets it, so a stale
 *  success can never suppress a channel for a selection it never ran against. */
export function createCompletionTracker() {
  let completed = new Set<TriggerChannel>();
  let lastSignature: string | null = null;
  return {
    sync(signature: string) {
      if (signature !== lastSignature) {
        completed = new Set();
        lastSignature = signature;
      }
    },
    isCompleted: (ch: TriggerChannel) => completed.has(ch),
    markCompleted: (ch: TriggerChannel) => { completed.add(ch); },
  };
}

export interface ChannelResult {
  ok: boolean;
  message: string;
}

export interface TriggerAllChannelConfig {
  key: "email" | "call";
  hasEligible: boolean;
  /** Must never throw — catch internally and resolve {ok:false, message}. */
  dispatch: () => Promise<ChannelResult>;
}

export interface TriggerAllDeps {
  hasWebFormWork: boolean;
  /** Must never throw — each item's failure is caught internally. */
  runWebForm: () => Promise<void>;
  channels: TriggerAllChannelConfig[];
  isCompleted: (ch: TriggerChannel) => boolean;
  markCompleted: (ch: TriggerChannel) => void;
  onPhaseChange: (phase: TriggerChannel | null) => void;
}

export interface TriggerAllOutcome {
  succeeded: { channel: "email" | "call"; message: string }[];
  failed: { channel: "email" | "call"; message: string }[];
  ranWebForm: boolean;
}

/** Web Form always runs first (while results are visible), then each
 *  channel in order — one channel failing never stops the others. */
export async function runTriggerAll(deps: TriggerAllDeps): Promise<TriggerAllOutcome> {
  let ranWebForm = false;
  if (deps.hasWebFormWork && !deps.isCompleted("webform")) {
    deps.onPhaseChange("webform");
    await deps.runWebForm();
    deps.markCompleted("webform");
    ranWebForm = true;
  }

  const succeeded: TriggerAllOutcome["succeeded"] = [];
  const failed: TriggerAllOutcome["failed"] = [];

  for (const ch of deps.channels) {
    if (deps.isCompleted(ch.key) || !ch.hasEligible) continue;
    deps.onPhaseChange(ch.key);
    const result = await ch.dispatch();
    if (result.ok) {
      deps.markCompleted(ch.key);
      succeeded.push({ channel: ch.key, message: result.message });
    } else {
      failed.push({ channel: ch.key, message: result.message });
    }
  }

  deps.onPhaseChange(null);
  return { succeeded, failed, ranWebForm };
}
