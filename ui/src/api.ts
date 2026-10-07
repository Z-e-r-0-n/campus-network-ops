export type RecordData = { id: string; revision: number; [key: string]: any };
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function api<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
  revision?: number,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (method !== "GET") {
    headers["X-CSRF-Token"] = decodeURIComponent(
      document.cookie
        .split("; ")
        .find((c) => c.startsWith("campus_csrf="))
        ?.split("=")[1] ?? "",
    );
    headers["Idempotency-Key"] = crypto.randomUUID();
    if (revision) headers["If-Match"] = String(revision);
  }
  const response = await fetch("/api/v1" + path, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok)
    throw new ApiError(
      response.status,
      data.fields?.length
        ? data.fields
            .map(
              (field: { field: string; message: string }) =>
                `${field.field.replace(/^body\.?/, "") || "Form"}: ${field.message}`,
            )
            .join("; ")
        : (data.message ??
            data.detail ??
            "The request could not be completed."),
    );
  return data;
}
export function time(value?: string) {
  if (!value) return "Not measured";
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
export function label(value: string) {
  return value.replaceAll("_", " ").replaceAll(".", " · ");
}
