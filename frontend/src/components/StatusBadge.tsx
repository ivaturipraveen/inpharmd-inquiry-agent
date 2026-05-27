import { FC } from "react";
import type { InquiryStatus } from "../types";

const LABEL: Record<InquiryStatus, string> = {
  draft: "Draft",
  email_sent: "Email Sent",
  email_responded: "Email Responded",
  call_pending: "Call Pending",
  call_completed: "Call Completed",
  closed: "Closed",
  failed: "Failed",
};

const TONE: Record<InquiryStatus, string> = {
  draft: "tone-neutral",
  email_sent: "tone-blue",
  email_responded: "tone-green",
  call_pending: "tone-amber",
  call_completed: "tone-green",
  closed: "tone-muted",
  failed: "tone-red",
};

const StatusBadge: FC<{ status: InquiryStatus }> = ({ status }) => (
  <span className={`status-badge ${TONE[status]}`}>{LABEL[status]}</span>
);

export default StatusBadge;
