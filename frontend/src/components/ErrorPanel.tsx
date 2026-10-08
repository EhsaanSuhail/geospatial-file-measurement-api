import type { ApiError, FileInfo } from "../api";

interface Props {
  error: ApiError;
  failedFile: FileInfo | null;
}

export function ErrorPanel({ error, failedFile }: Props) {
  return (
    <section className="card card-error" role="alert">
      <h2>The file could not be processed</h2>
      <p className="error-message">{error.message}</p>
      <p className="muted small">
        {error.status === 0 ? "No response from the API" : `HTTP ${error.status}`} ·{" "}
        <code>{error.code}</code>
      </p>
      {failedFile && (
        <p className="muted small">
          The API stored this upload with the status FAILED; its record is shown below.
        </p>
      )}
    </section>
  );
}
