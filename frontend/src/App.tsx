import { Fragment, useCallback, useEffect, useState } from "react";

import {
  type ApiHealth,
  ApiError,
  checkHealth,
  type FeatureList,
  type FileInfo,
  getFeatures,
  getFile,
  getMeasurements,
  type MeasurementList,
  uploadFile,
} from "./api";
import { ApiStatus } from "./components/ApiStatus";
import { ErrorPanel } from "./components/ErrorPanel";
import { FileSummary } from "./components/FileSummary";
import { MeasurementsTable } from "./components/MeasurementsTable";
import { type BusyState, UploadCard } from "./components/UploadCard";

interface Result {
  info: FileInfo;
  measurements: MeasurementList;
  features: FeatureList;
}

// Listed in the footer; hovering a name shows the exact endpoint.
const ENDPOINTS = [
  ["Upload", "POST /api/v1/files"],
  ["File details", "GET /api/v1/files/{id}"],
  ["Features", "GET /api/v1/files/{id}/features"],
  ["Measurements", "GET /api/v1/files/{id}/measurements"],
  ["Health", "GET /health"],
] as const;

export default function App() {
  const [health, setHealth] = useState<ApiHealth | "checking">("checking");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState<BusyState>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [failedFile, setFailedFile] = useState<FileInfo | null>(null);

  const refreshHealth = useCallback(() => {
    setHealth("checking");
    void checkHealth().then(setHealth);
  }, []);

  useEffect(() => refreshHealth(), [refreshHealth]);

  async function upload() {
    if (file === null) return;
    setBusy("uploading");
    setResult(null);
    setError(null);
    setFailedFile(null);
    try {
      const created = await uploadFile(file);
      setBusy("loading");
      const [info, measurements, features] = await Promise.all([
        getFile(created.id),
        getMeasurements(created.id),
        getFeatures(created.id),
      ]);
      setResult({ info, measurements, features });
      setHealth("ok");
    } catch (caught) {
      const apiError =
        caught instanceof ApiError
          ? caught
          : new ApiError(0, "UNEXPECTED_ERROR", "Something went wrong while calling the API.");
      setError(apiError);
      if (apiError.status === 0) refreshHealth();
      if (apiError.fileId) {
        // Processing failed after the upload was recorded: show the stored FAILED record.
        getFile(apiError.fileId).then(setFailedFile, () => undefined);
      }
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="page">
      <header className="header">
        <div>
          <h1>
            Geospatial Measurement API <span className="title-suffix">— Demo Interface</span>
          </h1>
          <p className="tagline">
            Upload a geospatial file and explore the measurements returned by the API.
          </p>
        </div>
        <div className="header-actions">
          <ApiStatus health={health} onRetry={refreshHealth} />
          <a className="button secondary" href="/docs" target="_blank" rel="noreferrer">
            Open API Docs
          </a>
        </div>
      </header>

      <div className="notice">
        <strong>
          Demo interface — the core geospatial processing is implemented in the backend API.
        </strong>
        <p>
          This page only uploads your file and displays the API's response. File validation, CRS
          handling, geospatial processing, measurement calculation and database operations are all
          performed by the backend.
        </p>
      </div>

      <main className="layout">
        <UploadCard file={file} busy={busy} onFileChange={setFile} onUpload={() => void upload()} />

        <div className="results">
          {error && <ErrorPanel error={error} failedFile={failedFile} />}
          {failedFile && <FileSummary info={failedFile} />}
          {result && <FileSummary info={result.info} />}
          {!result && !error && (
            <section className="card empty-state">
              <h2>2. File information</h2>
              <p className="muted">
                {busy === null
                  ? "The file details and its measurements will appear here after an upload."
                  : "The API is processing the file…"}
              </p>
            </section>
          )}
        </div>

        {result && (
          <div className="full-row">
            <MeasurementsTable measurements={result.measurements} features={result.features} />
          </div>
        )}
      </main>

      <footer className="footer">
        Backend endpoints used:{" "}
        {ENDPOINTS.map(([label, endpoint], index) => (
          <Fragment key={label}>
            {index > 0 && " · "}
            <span title={endpoint}>{label}</span>
          </Fragment>
        ))}
      </footer>
    </div>
  );
}
