const apiBaseUrl = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");

export function apiUrl(path) {
  return `${apiBaseUrl}${path}`;
}
