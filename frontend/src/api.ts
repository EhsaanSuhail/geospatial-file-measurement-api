// Small client for the backend API. The demo only calls these endpoints and displays the
// responses; validation, CRS handling and all measurements happen in the backend.

export type FileStatus = "PROCESSING" | "COMPLETED" | "FAILED";
export type MeasurementStatus = "CALCULATED" | "NOT_APPLICABLE" | "UNSUPPORTED" | "FAILED";

export interface FileInfo {
  id: string;
  filename: string;
  format: "KML" | "SHAPEFILE";
  file_size_bytes: number;
  crs: string | null;
  feature_count: number;
  status: FileStatus;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface Measurement {
  feature_id: string;
  feature_index: number;
  geometry_type: string | null;
  measurement_type: "AREA" | "LENGTH" | "NONE";
  value: number | null;
  unit: string | null;
  measurement_crs: string | null;
  status: MeasurementStatus;
  error_message: string | null;
}

export interface MeasurementList {
  file_id: string;
  file_status: FileStatus;
  total: number;
  summary: { calculated: number; not_applicable: number; unsupported: number; failed: number };
  items: Measurement[];
}

export interface Feature {
  id: string;
  feature_index: number;
  layer: string;
  geometry_type: string | null;
  geometry_error: string | null;
  properties: Record<string, unknown>;
}

export interface FeatureList {
  total: number;
  items: Feature[];
}

export type ApiHealth = "ok" | "database-unavailable" | "unreachable";

export class ApiError extends Error {
  constructor(
    readonly status: number, // 0 when the backend could not be reached
    readonly code: string,
    message: string,
    readonly fileId?: string, // set when the file record exists (processing failed later)
  ) {
    super(message);
  }
}

// The largest page the API returns; the demo shows the first page only.
const PAGE_SIZE = 1000;

export function uploadFile(file: File): Promise<FileInfo> {
  const form = new FormData();
  form.append("file", file);
  return request<FileInfo>("/api/v1/files", { method: "POST", body: form });
}

export function getFile(id: string): Promise<FileInfo> {
  return request<FileInfo>(`/api/v1/files/${id}`);
}

export function getMeasurements(id: string): Promise<MeasurementList> {
  return request<MeasurementList>(`/api/v1/files/${id}/measurements?limit=${PAGE_SIZE}`);
}

export function getFeatures(id: string): Promise<FeatureList> {
  return request<FeatureList>(`/api/v1/files/${id}/features?limit=${PAGE_SIZE}`);
}

export async function checkHealth(): Promise<ApiHealth> {
  try {
    // A stopped backend can make the proxy wait several seconds; don't leave the status hanging.
    const response = await fetch("/health", { signal: AbortSignal.timeout(5000) });
    if (response.ok) return "ok";
    const body = (await response.json().catch(() => null)) as { database?: string } | null;
    return body?.database === "unavailable" ? "database-unavailable" : "unreachable";
  } catch {
    return "unreachable";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw unreachable();
  }

  const body: unknown = await response.json().catch(() => null);
  if (response.ok) return body as T;

  const error = readErrorEnvelope(body);
  if (error) {
    const fileId = error.details?.file_id;
    throw new ApiError(
      response.status,
      error.code,
      error.message,
      typeof fileId === "string" ? fileId : undefined,
    );
  }
  // Not the API's own error format: the dev server answers like this when the backend is down.
  if (response.status >= 500) throw unreachable();
  throw new ApiError(response.status, `HTTP_${response.status}`, "Unexpected response from the API.");
}

interface ErrorEnvelope {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

function readErrorEnvelope(body: unknown): ErrorEnvelope | null {
  if (typeof body !== "object" || body === null || !("error" in body)) return null;
  const error = (body as { error: unknown }).error;
  if (typeof error !== "object" || error === null) return null;
  const { code, message } = error as Partial<ErrorEnvelope>;
  return typeof code === "string" && typeof message === "string" ? (error as ErrorEnvelope) : null;
}

function unreachable(): ApiError {
  return new ApiError(
    0,
    "API_UNREACHABLE",
    "Could not reach the backend API. Make sure the backend is running.",
  );
}
