import type { FileInfo } from "../api";
import { formatBytes, formatDateTime } from "../format";
import { StatusBadge } from "./StatusBadge";

export function FileSummary({ info }: { info: FileInfo }) {
  return (
    <section className="card">
      <div className="card-header">
        <h2>2. File information</h2>
        <code className="source">GET /api/v1/files/{"{id}"}</code>
      </div>

      <dl className="summary">
        <div>
          <dt>File name</dt>
          <dd className="file-name">{info.filename}</dd>
        </div>
        <div>
          <dt>Processing status</dt>
          <dd>
            <StatusBadge status={info.status} />
          </dd>
        </div>
        <div>
          <dt>Format</dt>
          <dd>{info.format === "KML" ? "KML" : "Shapefile (ZIP)"}</dd>
        </div>
        <div>
          <dt>Source CRS</dt>
          <dd>{sourceCrs(info)}</dd>
        </div>
        <div>
          <dt>Feature count</dt>
          <dd>{info.feature_count}</dd>
        </div>
        <div>
          <dt>File size</dt>
          <dd>{formatBytes(info.file_size_bytes)}</dd>
        </div>
        <div>
          <dt>Uploaded</dt>
          <dd>{formatDateTime(info.created_at)}</dd>
        </div>
        <div>
          <dt>File ID</dt>
          <dd className="mono small">{info.id}</dd>
        </div>
      </dl>
    </section>
  );
}

function sourceCrs(info: FileInfo) {
  if (info.crs === null) {
    const text = info.status === "COMPLETED" ? "Not defined in the file" : "—";
    return <span className="muted">{text}</span>;
  }
  // An unidentified CRS is reported as WKT, which is too long to show in full here.
  if (info.crs.length > 40) return <span title={info.crs}>Custom CRS (WKT)</span>;
  return <span className="mono">{info.crs}</span>;
}
