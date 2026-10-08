import type { FileStatus, MeasurementStatus } from "../api";
import { toLabel } from "../format";

type Status = FileStatus | MeasurementStatus;

const TONES: Record<Status, "success" | "neutral" | "warning" | "danger"> = {
  COMPLETED: "success",
  CALCULATED: "success",
  NOT_APPLICABLE: "neutral",
  PROCESSING: "warning",
  UNSUPPORTED: "warning",
  FAILED: "danger",
};

export function StatusBadge({ status }: { status: Status }) {
  return <span className={`badge badge-${TONES[status]}`}>{toLabel(status)}</span>;
}
