import { useRef, useState } from "react";

import { formatBytes } from "../format";

export type BusyState = "uploading" | "loading" | null;

interface Props {
  file: File | null;
  busy: BusyState;
  onFileChange: (file: File | null) => void;
  onUpload: () => void;
}

export function UploadCard({ file, busy, onFileChange, onUpload }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function openPicker() {
    if (busy === null) inputRef.current?.click();
  }

  return (
    <section className="card">
      <h2>1. Upload a file</h2>
      <p className="muted">A KML file (.kml) or a ZIP archive containing one Shapefile (.zip).</p>

      <div
        className={`dropzone${dragging ? " dropzone-active" : ""}`}
        role="button"
        tabIndex={0}
        aria-label="Choose a KML or Shapefile ZIP file"
        onClick={openPicker}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openPicker();
          }
        }}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (busy === null) onFileChange(event.dataTransfer.files[0] ?? null);
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".kml,.zip"
          hidden
          data-testid="file-input"
          onChange={(event) => {
            onFileChange(event.target.files?.[0] ?? null);
            event.target.value = ""; // allows choosing the same file again
          }}
        />
        {file ? (
          <>
            <strong className="file-name">{file.name}</strong>
            <span className="muted">{formatBytes(file.size)} · click to choose another file</span>
          </>
        ) : (
          <>
            <strong>Drop a file here, or click to choose one</strong>
            <span className="muted">.kml or .zip, up to 50 MB</span>
          </>
        )}
      </div>

      <button
        type="button"
        className="button primary full-width"
        disabled={file === null || busy !== null}
        aria-busy={busy !== null}
        onClick={onUpload}
      >
        {busy === null ? (
          "Upload and process"
        ) : (
          <>
            <span className="spinner" aria-hidden="true" />
            {busy === "uploading" ? "Uploading and processing…" : "Loading results…"}
          </>
        )}
      </button>

      <p className="hint">
        Sample files are in the <code>sample_data/</code> folder of the repository.
      </p>
    </section>
  );
}
