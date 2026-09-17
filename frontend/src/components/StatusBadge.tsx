import { FC } from "react";
import type { InquiryStatus } from "../types";

const LABEL: Record<InquiryStatus, string> = {
  draft: "Draft",
  email_pending: "Scheduled",
  email_sent: "Email Sent",
  email_responded: "Email Responded",
  call_pending: "Call Pending",
  call_completed: "Call Completed",
  call_scheduled: "Call Scheduled",
  needs_attention: "Needs Attention",
  closed: "Closed",
  failed: "Failed",
};

const TONE: Record<InquiryStatus, string> = {
  draft: "tone-neutral",
  email_pending: "tone-amber",
  email_sent: "tone-blue",
  email_responded: "tone-green",
  call_pending: "tone-amber",
  call_completed: "tone-green",
  call_scheduled: "tone-amber",
  needs_attention: "tone-red",
  closed: "tone-muted",
  failed: "tone-red",
};

export function isRetryScheduled(
  status: InquiryStatus,
  nextRetryAt?: string | null,
  retryCount?: number | null,
  maxRetries?: number | null
): boolean {
  return (
    status === "call_completed" &&
    !!nextRetryAt &&
    (retryCount ?? 0) < (maxRetries ?? 2)
  );
}

interface Props {
  status: InquiryStatus;
  next_retry_at?: string | null;
  retry_count?: number | null;
  max_retries?: number | null;
}

const StatusBadge: FC<Props> = ({ status, next_retry_at, retry_count, max_retries }) => {
  const retryScheduled = isRetryScheduled(status, next_retry_at, retry_count, max_retries);
  const label = retryScheduled ? "Retry Scheduled" : LABEL[status];
  const tone = retryScheduled ? "tone-amber" : TONE[status];
  return <span className={`status-badge ${tone}`}>{label}</span>;
};

export default StatusBadge;
