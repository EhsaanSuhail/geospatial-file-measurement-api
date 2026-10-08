// Display formatting only. Measurement values are shown as the API returns them, in the API's
// units: the frontend does not convert or calculate anything.

import type { Feature } from "./api";

export function formatNumber(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

// "NOT_APPLICABLE" -> "Not applicable"
export function toLabel(value: string): string {
  const words = value.toLowerCase().replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// KML placemarks usually have a "Name" property; Shapefiles may not have one.
export function featureName(feature: Feature | undefined): string | undefined {
  const properties = feature?.properties ?? {};
  const name = properties.Name ?? properties.name ?? properties.NAME;
  return typeof name === "string" && name.trim() !== "" ? name : undefined;
}
