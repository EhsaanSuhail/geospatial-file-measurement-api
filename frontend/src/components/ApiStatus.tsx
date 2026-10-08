import type { ApiHealth } from "../api";

const LABELS: Record<ApiHealth | "checking", string> = {
  checking: "Checking API…",
  ok: "API connected",
  "database-unavailable": "Database unavailable",
  unreachable: "API not reachable",
};

interface Props {
  health: ApiHealth | "checking";
  onRetry: () => void;
}

export function ApiStatus({ health, onRetry }: Props) {
  return (
    <div className={`api-status api-status-${health}`} aria-live="polite">
      <span className="dot" aria-hidden="true" />
      {LABELS[health]}
      {(health === "unreachable" || health === "database-unavailable") && (
        <button type="button" className="link-button" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
